"""Explicit RDF/OWL to Neo4j Ontology Graph Specification projection (no reasoner)."""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator
from rdflib import BNode, Graph, Literal, Namespace, OWL, RDF, RDFS, SKOS, URIRef, XSD

SCHEMA_URL = 'https://neo4j.com/docs/ontology-graph-spec/1.0.0/schema.json'
SCHEMA_PATH = Path(__file__).parent / 'schemas/ontology-graph-spec-1.0.0.schema.json'
OBO = Namespace('http://purl.obolibrary.org/obo/')
OIO = Namespace('http://www.geneontology.org/formats/oboInOwl#')


from rdf_loader import load_rdf


def local_name(uri):
    value = str(uri).rsplit('#', 1)[-1].rsplit('/', 1)[-1]
    if str(uri).startswith('urn:') and '#' not in str(uri):
        value = value.rsplit(':', 1)[-1]
    return value or 'Resource'


def text(graph, subject, predicates):
    for predicate in predicates:
        values = [v for v in graph.objects(subject, predicate) if isinstance(v, Literal)]
        if values:
            return str(sorted(values, key=lambda v: (v.language not in ('en', None), str(v)))[0])
    return ''


def identifier(value):
    value = re.sub(r'[^A-Za-z0-9_]', '_', value)
    return ('_' + value) if value[:1].isdigit() else value or 'Resource'


def convert(graph, name='RDF ontology', include_deprecated=False, class_iris=None):
    """Return (model, report). class_iris selects an explicit subset, never silently truncates.

    Classes become node types. Subclass edges are explicit IS_A relationships, not
    inherited Neo4j labels. Restrictions are projections, not logical equivalences.
    """
    counts = Counter()
    classes = {s for t in (OWL.Class, RDFS.Class) for s in graph.subjects(RDF.type, t)
               if isinstance(s, URIRef)}
    for s, o in graph.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef):
            classes.add(s)
        if isinstance(o, URIRef):
            classes.add(o)
    counts['discovered_classes'] = len(classes)
    deprecated = {c for c in classes if any(str(v).lower() in ('true', '1')
                                           for v in graph.objects(c, OWL.deprecated))}
    if not include_deprecated:
        classes -= deprecated
    counts['deprecated_classes_excluded'] = 0 if include_deprecated else len(deprecated)
    if class_iris is not None:
        classes &= {URIRef(c) for c in class_iris}
    classes = sorted(classes, key=str)
    ids = {c: f'n:{i}' for i, c in enumerate(classes)}
    # Stable URI-based identifiers avoid renaming when labels/languages change.
    names = {c: identifier(local_name(c)) for c in classes}
    duplicates = Counter(names.values())
    for c in classes:
        if duplicates[names[c]] > 1:
            names[c] += '_' + hashlib.sha256(str(c).encode()).hexdigest()[:12]
    model = {'schema': SCHEMA_URL, 'version': '1',
             'id': re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'ontology',
             'name': name, 'description': 'RDF/OWL projection; see accompanying conversion report.',
             'nodes': {}, 'relationships': {}}
    for c in classes:
        label = text(graph, c, (RDFS.label, SKOS.prefLabel))
        aliases = {str(v) for p in (RDFS.label, SKOS.prefLabel, SKOS.altLabel,
                                    OIO.hasExactSynonym, OIO.hasRelatedSynonym)
                   for v in graph.objects(c, p) if isinstance(v, Literal)}
        definition = text(graph, c, (OBO.IAO_0000115, SKOS.definition, RDFS.comment))
        model['nodes'][ids[c]] = {'labels': {'identifier': names[c]}, 'reference': str(c),
                                 'description': '\n\n'.join(filter(None, (label, definition))),
                                 'aliases': sorted(aliases)}
    edges = set()

    def edge(source, predicate, target, origin):
        if source in ids and target in ids:
            edges.add((str(source), str(predicate), str(target), origin))
        else:
            counts['relationships_with_excluded_or_unknown_endpoints'] += 1

    for c in classes:
        for parent in graph.objects(c, RDFS.subClassOf):
            if isinstance(parent, URIRef):
                edge(c, RDFS.subClassOf, parent, 'named subclass axiom')
            elif isinstance(parent, BNode):
                prop = graph.value(parent, OWL.onProperty)
                filler = graph.value(parent, OWL.someValuesFrom)
                if isinstance(prop, URIRef) and isinstance(filler, URIRef) and (parent, RDF.type, OWL.Restriction) in graph:
                    edge(c, prop, filler, 'existential restriction: someValuesFrom')
                else:
                    counts['unsupported_subclass_expressions'] += 1
    object_props = sorted(set(graph.subjects(RDF.type, OWL.ObjectProperty)), key=str)
    datatype_props = sorted(set(graph.subjects(RDF.type, OWL.DatatypeProperty)), key=str)
    # Include properties used only in restrictions; URI hash prevents collisions.
    predicates = {URIRef(e[1]) for e in edges} | set(object_props)
    rel_names = {p: ('IS_A' if p == RDFS.subClassOf else identifier(text(graph, p, (RDFS.label, SKOS.prefLabel)) or local_name(p)).upper()) for p in predicates}
    duplicate_rels = Counter(rel_names.values())
    for p in rel_names:
        if duplicate_rels[rel_names[p]] > 1:
            rel_names[p] += '_' + hashlib.sha256(str(p).encode()).hexdigest()[:12]
    for p in object_props:
        domains, ranges = list(graph.objects(p, RDFS.domain)), list(graph.objects(p, RDFS.range))
        if len(domains) == len(ranges) == 1 and all(isinstance(x, URIRef) for x in domains + ranges):
            edge(domains[0], p, ranges[0], 'object property domain/range projection')
        else:
            counts['object_properties_without_single_named_domain_and_range'] += 1
    types = {XSD.string: 'STRING', XSD.boolean: 'BOOLEAN', XSD.integer: 'INTEGER',
             XSD.int: 'INTEGER', XSD.long: 'INTEGER', XSD.decimal: 'FLOAT',
             XSD.float: 'FLOAT', XSD.double: 'FLOAT', XSD.date: 'DATE',
             XSD.dateTime: 'LOCAL DATETIME', XSD.dateTimeStamp: 'ZONED DATETIME',
             XSD.anyURI: 'STRING'}
    for i, p in enumerate(datatype_props):
        domains, ranges = list(graph.objects(p, RDFS.domain)), list(graph.objects(p, RDFS.range))
        if len(domains) != 1 or domains[0] not in ids:
            counts['datatype_properties_without_single_selected_domain'] += 1
            continue
        dtype = types.get(ranges[0]) if len(ranges) == 1 else None
        if dtype is None:
            dtype = 'ANY'
            counts['datatype_properties_with_unmapped_range'] += 1
        # OWL datatype properties can have multiple values: preserve this as a list.
        if (p, RDF.type, OWL.FunctionalProperty) not in graph:
            dtype = f'LIST<{dtype}>'
        node = model['nodes'][ids[domains[0]]]
        node.setdefault('properties', {})[f'p:{i}'] = {
            'name': identifier(local_name(p)) + '_' + hashlib.sha256(str(p).encode()).hexdigest()[:8],
            'type': dtype, 'reference': str(p),
            'description': text(graph, p, (RDFS.label, RDFS.comment))}
        counts['datatype_properties_exported'] += 1
    for i, (s, p, o, origin) in enumerate(sorted(edges)):
        predicate = URIRef(p)
        model['relationships'][f'r:{i}'] = {
            'type': rel_names[predicate], 'from': {'node': ids[URIRef(s)]},
            'to': {'node': ids[URIRef(o)]}, 'reference': p,
            'description': origin + ': ' + (text(graph, predicate, (RDFS.label,)) or local_name(p)),
            'extensions': {'custom': {'owlProjection': origin}}}
    for predicate in (OWL.equivalentClass, OWL.disjointWith, OWL.unionOf, OWL.intersectionOf,
                      OWL.allValuesFrom, OWL.hasValue, OWL.cardinality, OWL.minCardinality,
                      OWL.maxCardinality, OWL.qualifiedCardinality, OWL.minQualifiedCardinality,
                      OWL.maxQualifiedCardinality, OWL.imports, OWL.inverseOf, OWL.propertyChainAxiom):
        counts['untranslated_axioms:' + local_name(predicate)] = len(list(graph.triples((None, predicate, None))))
    report = {'counts': dict(counts), 'triples': len(graph), 'nodes': len(ids),
              'relationships': len(edges), 'limitations': [
                  'Schema projection only; no individuals or instance data are exported.',
                  'No OWL reasoning, import fetching, or inherited property propagation.',
                  'IS_A and existential restrictions are explicit relationship projections; they do not preserve full OWL semantics.',
                  'Object property domain/range projection is a modeling convention, not an asserted instance edge.',
                  'Universal restrictions and complex anonymous expressions are not converted.',
                  'OWL decimal maps to FLOAT (precision may differ); dateTime maps to LOCAL DATETIME (review timezone needs).',
                  'Aura upload size/model limits are not verified by JSON schema validation.'
              ]}
    # Match the successfully uploaded Transport-Test profile. Keep richer source
    # metadata in the sidecar, outside Aura's import document.
    report['source_metadata'] = {'nodes': {}, 'relationships': {}, 'properties': {}}
    for i, (node_id, node) in enumerate(model['nodes'].items()):
        reference = node.pop('reference')
        aliases = node.pop('aliases')
        report['source_metadata']['nodes'][node_id] = {'reference': reference, 'aliases': aliases}
        extra_aliases = [a for a in aliases if a not in node['description']]
        if extra_aliases:
            node['description'] += '\n\nSynonyms: ' + '; '.join(extra_aliases)
        node['description'] += '\n\nSource: ' + reference
        node.setdefault('properties', {})
        node.setdefault('constraints', {})
        node['extensions'] = {'display': {'x': (i % 200) * 250, 'y': (i // 200) * 180}}
        for prop_id, prop in node['properties'].items():
            report['source_metadata']['properties'][prop_id] = {'reference': prop.pop('reference')}
    for rel_id, rel in model['relationships'].items():
        report['source_metadata']['relationships'][rel_id] = {
            'reference': rel.pop('reference'), **rel.pop('extensions')['custom']}
    return model, report


def validate(model, schema_path=SCHEMA_PATH):
    schema = json.loads(Path(schema_path).read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(model)
    nodes = model.get('nodes', {})
    for rel in model.get('relationships', {}).values():
        for end in ('from', 'to'):
            if rel[end].get('node') not in nodes:
                raise ValueError(f'Dangling relationship endpoint: {rel[end]}')
    for entity in list(nodes.values()) + list(model.get('relationships', {}).values()):
        for constraint in entity.get('constraints', {}).values():
            if not set(constraint['properties']) <= set(entity.get('properties', {})):
                raise ValueError('Constraint refers to an unknown property')
    return True


def normalize_upload_types(model, report):
    """Aura's upload decoder rejects ANY even though the published schema permits it.

    This only changes schema definitions. A separate data importer must stringify
    values for these properties; no source RDF values are modified here.
    """
    conversions = report.setdefault('upload_type_coercions', [])
    for collection in ('nodes', 'relationships'):
        for entity_id, entity in model.get(collection, {}).items():
            for prop_id, prop in entity.get('properties', {}).items():
                original = prop.get('type')
                if original not in ('ANY', 'LIST<ANY>'):
                    continue
                replacement = 'LIST<STRING>' if original == 'LIST<ANY>' else 'STRING'
                prop['type'] = replacement
                note = ('Upload compatibility: ambiguous or unsupported source types are represented '
                        'as strings. Data imports must stringify these values; source RDF is unchanged.')
                prop['description'] = (prop.get('description', '') + '\n' + note).strip()
                conversions.append({'entity': f'{collection}.{entity_id}', 'property': prop_id,
                                    'name': prop.get('name', prop_id), 'from': original, 'to': replacement})
    if not conversions:
        report.pop('upload_type_coercions', None)
    return model, report


def write_output(model, report, output_path):
    normalize_upload_types(model, report)
    validate(model)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(model, ensure_ascii=False, indent=2) + '\n')
    report_path = output_path.with_suffix('.report.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return output_path, report_path
