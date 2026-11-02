"""File-path-only RDF to Aura schema pipeline using domain-independent rules."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from rdflib import BNode, Literal, Namespace, OWL, RDF, RDFS, SKOS, URIRef, XSD
from ontology_converter import SCHEMA_URL, identifier, local_name, text, validate, write_output

import rdf_loader

SH = Namespace('http://www.w3.org/ns/shacl#')
META_NAMESPACES = tuple(map(str, (RDF, RDFS, OWL, XSD, SH)))
INTEGER_TYPES = {XSD[n] for n in ('integer', 'int', 'long', 'short', 'byte', 'nonNegativeInteger',
    'positiveInteger', 'nonPositiveInteger', 'negativeInteger', 'unsignedLong', 'unsignedInt', 'unsignedShort', 'unsignedByte')}
DATATYPES = {XSD.string: 'STRING', RDF.langString: 'STRING', XSD.anyURI: 'STRING',
    XSD.boolean: 'BOOLEAN', XSD.float: 'FLOAT', XSD.double: 'FLOAT', XSD.decimal: 'FLOAT',
    XSD.date: 'DATE', XSD.dateTimeStamp: 'ZONED DATETIME', XSD.duration: 'DURATION',
    XSD.dayTimeDuration: 'DURATION', XSD.yearMonthDuration: 'DURATION',
    **{t: 'INTEGER' for t in INTEGER_TYPES}}


def unique_names(uris, graph, upper=False):
    result = {u: identifier((text(graph, u, (RDFS.label, SKOS.prefLabel)) if upper else '') or local_name(u)) for u in uris}
    if upper:
        result = {u: name.upper() for u, name in result.items()}
    counts = Counter(result.values())
    used = set()
    for uri in sorted(result, key=str):
        base = result[uri]
        value = base if counts[base] == 1 else base + '_' + hashlib.sha256(str(uri).encode()).hexdigest()[:12]
        while value in used:
            value += '_'
        used.add(value); result[uri] = value
    return result


def is_class(uri):
    return isinstance(uri, URIRef) and not str(uri).startswith(META_NAMESPACES)


def literal_type(value):
    if value.datatype == XSD.dateTime:
        return 'ZONED DATETIME' if re.search(r'(Z|[+-]\d\d:\d\d)$', str(value)) else 'LOCAL DATETIME'
    if value.datatype == XSD.time:
        return 'ZONED TIME' if re.search(r'(Z|[+-]\d\d:\d\d)$', str(value)) else 'LOCAL TIME'
    return DATATYPES.get(value.datatype, 'STRING' if value.datatype is None or value.language else 'ANY')


def general_schema(graph, name, progress=None, validate_model=True):
    """Declared schema plus observed signatures; never export record values."""
    classes = {s for t in (OWL.Class, RDFS.Class) for s in graph.subjects(RDF.type, t) if isinstance(s, URIRef)}
    schema_resources = set(classes)
    for kind in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty, RDF.Property,
                 OWL.Ontology, SH.NodeShape, SH.PropertyShape):
        schema_resources.update(graph.subjects(RDF.type, kind))
    for p in (RDFS.subClassOf, OWL.equivalentClass):
        for s, o in graph.subject_objects(p):
            if isinstance(s, URIRef): classes.add(s)
            if isinstance(o, URIRef): classes.add(o)
    for p in (RDFS.domain, RDFS.range, SH.targetClass, SH['class'], OWL.someValuesFrom, OWL.allValuesFrom, OWL.onClass):
        for s, o in graph.subject_objects(p):
            if is_class(o): classes.add(o)
    for head in graph.objects(None, OWL.intersectionOf):
        try:
            classes.update(c for c in graph.items(head) if is_class(c))
        except ValueError:
            pass  # The expression walker reports malformed lists.
    schema_resources.update(classes)
    schema_resources.update(graph.predicates())
    schema_resources.update(graph.objects(None, OWL.onProperty))
    schema_resources.update(graph.subjects(RDFS.domain, None))
    schema_resources.update(graph.subjects(RDFS.range, None))
    # rdf:type identifies data types even when owl:Class declarations are absent.
    resource_types = defaultdict(set)
    for subject, kind in graph.subject_objects(RDF.type):
        if is_class(kind) and subject not in schema_resources:
            classes.add(kind); resource_types[subject].add(kind)
    schema_resources.update(classes)
    deprecated = {c for c in classes if any(str(v).lower() in ('true', '1') for v in graph.objects(c, OWL.deprecated))}
    classes -= deprecated
    for subject in resource_types:
        resource_types[subject] &= classes
    # Preserve untyped data in an explicitly inferred generic Resource type.
    fallback = URIRef('urn:aura-converter:Resource')
    untyped = set()
    for s, p, o in graph.triples((None, None, None)):
        if s in schema_resources or s in deprecated or not isinstance(s, (URIRef, BNode)):
            continue
        if not str(p).startswith(META_NAMESPACES) or p in (RDFS.label, RDFS.comment):
            if not resource_types[s]: untyped.add(s)
            if isinstance(o, (URIRef, BNode)) and o not in schema_resources and not resource_types[o]:
                untyped.add(o)
    if untyped:
        classes.add(fallback)
        for s in untyped: resource_types[s].add(fallback)
    if not classes:
        raise ValueError('No declared classes, typed records, or inferable RDF records were found. No empty ontology was written.')
    classes = sorted(classes, key=str)
    ids = {c: f'n:{i}' for i, c in enumerate(classes)}
    names = unique_names(classes, graph)
    model = {'schema': SCHEMA_URL, 'version': '1', 'id': identifier(name).lower(), 'name': name,
             'description': 'RDF schema projection; declared and observed evidence are distinguished in the companion report.',
             'nodes': {}, 'relationships': {}}
    for i, c in enumerate(classes):
        description = '\n\n'.join(filter(None, [text(graph, c, (RDFS.label, SKOS.prefLabel)),
                    text(graph, c, (SKOS.definition, RDFS.comment)), 'Source: ' + str(c)]))
        model['nodes'][ids[c]] = {'labels': {'identifier': names[c]}, 'description': description,
            'properties': {}, 'constraints': {}, 'extensions': {'display': {'x': (i % 20) * 300, 'y': (i // 20) * 220}}}
    edges = defaultdict(set)
    properties = {}
    warnings = Counter()
    restrictions = []

    def edge(a, p, b, reason):
        if a in ids and b in ids:
            edges[(a, p, b)].add(reason)
        else:
            warnings['relationship_endpoint_not_a_selected_named_class'] += 1

    def property_def(owner, predicate, dtype=None, multiple=None, reason='declared'):
        if owner not in ids: return
        item = properties.setdefault((owner, predicate), {'types': set(), 'multiple': False, 'evidence': set()})
        item['types'].add(dtype or 'ANY')
        item['multiple'] |= bool(multiple)
        item['evidence'].add(reason)

    def expression(owner, expr, reason, visited):
        if expr in visited: return
        visited = visited | {expr}
        if isinstance(expr, URIRef):
            edge(owner, RDFS.subClassOf, expr, reason); return
        if not isinstance(expr, BNode): return
        for head in graph.objects(expr, OWL.intersectionOf):
            try:
                for member in graph.items(head): expression(owner, member, reason, visited)
            except ValueError:
                warnings['malformed_rdf_list'] += 1
        for p in (OWL.unionOf, OWL.complementOf, OWL.oneOf):
            if (expr, p, None) in graph: warnings['unsupported_expression:' + local_name(p)] += 1
        predicate = graph.value(expr, OWL.onProperty)
        if predicate is None: return
        if not isinstance(predicate, URIRef):
            warnings['anonymous_property_expression'] += 1; return
        for p in (OWL.cardinality, OWL.minCardinality, OWL.maxCardinality, OWL.qualifiedCardinality,
                  OWL.minQualifiedCardinality, OWL.maxQualifiedCardinality, OWL.allValuesFrom, OWL.hasValue):
            for value in graph.objects(expr, p):
                restrictions.append({'class': str(owner), 'property': str(predicate), 'restriction': str(p),
                                     'value': str(value), 'origin': reason,
                                     'on_class': str(graph.value(expr, OWL.onClass)) if graph.value(expr, OWL.onClass) is not None else None,
                                     'on_data_range': str(graph.value(expr, OWL.onDataRange)) if graph.value(expr, OWL.onDataRange) is not None else None})
        for target in graph.objects(expr, OWL.someValuesFrom):
            if target in ids:
                edge(owner, predicate, target, reason + ':someValuesFrom')
            elif isinstance(target, URIRef) and str(target).startswith(str(XSD)):
                property_def(owner, predicate, DATATYPES.get(target, 'ANY'), True, reason + ':someValuesFrom')
            else:
                warnings['unsupported_existential_filler'] += 1
    for c in classes:
        for p in (RDFS.subClassOf, OWL.equivalentClass):
            for expr in graph.objects(c, p): expression(c, expr, local_name(p), set())
    declared_props = {s for t in (OWL.ObjectProperty, OWL.DatatypeProperty, RDF.Property)
                      for s in graph.subjects(RDF.type, t) if isinstance(s, URIRef)}
    for p in sorted(declared_props | set(graph.subjects(RDFS.domain, None)), key=str):
        domains = [d for d in graph.objects(p, RDFS.domain) if d in ids]
        ranges = list(graph.objects(p, RDFS.range))
        if len(list(graph.objects(p, RDFS.domain))) > 1 or len(ranges) > 1:
            warnings['multiple_domain_or_range_declarations_projected_as_signatures_not_unions'] += 1
        for domain in domains:
            if (p, RDF.type, OWL.DatatypeProperty) in graph or any(str(r).startswith(str(XSD)) or r == RDFS.Literal for r in ranges):
                dtype = DATATYPES.get(ranges[0], 'ANY') if len(ranges) == 1 else 'ANY'
                property_def(domain, p, dtype, (p, RDF.type, OWL.FunctionalProperty) not in graph)
            for target in ranges:
                if target in ids: edge(domain, p, target, 'domain/range')
        if not domains:
            warnings['property_without_named_domain'] += 1
    # Instance data supplies schema evidence only, never output values.
    observed = defaultdict(Counter)
    for subject, kinds in list(resource_types.items()):
        if not kinds or subject in schema_resources: continue
        for p, value in graph.predicate_objects(subject):
            if p == RDF.type: continue
            if isinstance(value, Literal):
                for owner in kinds:
                    property_def(owner, p, literal_type(value), False, 'observed')
                    observed[(owner, p)][subject] += 1
            elif isinstance(value, (URIRef, BNode)):
                for owner in kinds:
                    for target in resource_types.get(value, ()):
                        edge(owner, p, target, 'observed')
    for key, counts in observed.items():
        if max(counts.values()) > 1: properties[key]['multiple'] = True
    # SHACL constraints explicitly define validation requirements, unlike OWL keys.
    shape_constraints = []
    for shape, owner in graph.subject_objects(SH.targetClass):
        if owner not in ids: continue
        if any(str(v).lower() in ('true', '1') for v in graph.objects(shape, SH.deactivated)):
            continue
        for prop_shape in graph.objects(shape, SH.property):
            if any(str(v).lower() in ('true', '1') for v in graph.objects(prop_shape, SH.deactivated)):
                continue
            p = graph.value(prop_shape, SH.path)
            if not isinstance(p, URIRef):
                warnings['unsupported_shacl_path'] += 1; continue
            dtype = graph.value(prop_shape, SH.datatype)
            target = graph.value(prop_shape, SH['class'])
            minimum, maximum = graph.value(prop_shape, SH.minCount), graph.value(prop_shape, SH.maxCount)
            if target in ids:
                edge(owner, p, target, 'SHACL class/path')
                if minimum is not None or maximum is not None:
                    warnings['shacl_relationship_cardinality_retained_in_report_only'] += 1
            elif dtype is not None or (owner, p) in properties or graph.value(prop_shape, SH.nodeKind) == SH.Literal:
                property_def(owner, p, DATATYPES.get(dtype, 'ANY'), str(maximum) != '1', 'SHACL')
                if minimum is not None and int(minimum) > 0:
                    shape_constraints.append((owner, p, 'EXISTS'))
            else:
                warnings['shacl_property_without_literal_or_named_class_mapping'] += 1
            for pred, val in graph.predicate_objects(prop_shape):
                if pred not in (SH.path, SH.datatype, SH['class'], SH.minCount, SH.maxCount, RDF.type):
                    warnings['untranslated_shacl:' + local_name(pred)] += 1
    all_predicates = {p for _, p in properties} | {p for _, p, _ in edges}
    prop_names = unique_names(all_predicates, graph)
    rel_names = unique_names({p for _, p, _ in edges}, graph, upper=True)
    if RDFS.subClassOf in rel_names:
        for p in rel_names:
            if p != RDFS.subClassOf and rel_names[p] == 'IS_A':
                rel_names[p] += '_' + hashlib.sha256(str(p).encode()).hexdigest()[:12]
        rel_names[RDFS.subClassOf] = 'IS_A'
    property_ids = {}
    evidence = {'nodes': {ids[c]: str(c) for c in classes}, 'properties': {}, 'relationships': {}}
    for i, ((owner, p), info) in enumerate(sorted(properties.items(), key=lambda item: tuple(map(str, item[0])))):
        types = info['types'] - {'ANY'}
        dtype = next(iter(types)) if len(types) == 1 else ('FLOAT' if types and types <= {'INTEGER', 'FLOAT'} else 'ANY')
        if 'ANY' in info['types']: dtype = 'ANY'
        if info['multiple']: dtype = f'LIST<{dtype}>'
        key = f'p:{i}'; property_ids[(owner, p)] = key
        description = text(graph, p, (RDFS.comment, SKOS.definition, RDFS.label))
        if (owner, p) in observed:
            description += '\nType and multiplicity partly inferred from observed records; presence is not required.'
        description += '\nSource: ' + str(p)
        model['nodes'][ids[owner]]['properties'][key] = {'name': prop_names[p], 'type': dtype, 'description': description.strip()}
        evidence['properties'][key] = {'predicate': str(p), 'owner': str(owner), 'evidence': sorted(info['evidence'])}
    for i, (owner, p, kind) in enumerate(shape_constraints):
        key = property_ids[(owner, p)]
        model['nodes'][ids[owner]]['constraints'][f'c:{i}'] = {'type': kind, 'properties': [key]}
    for i, ((a, p, b), reasons) in enumerate(sorted(edges.items(), key=lambda item: tuple(map(str, item[0])))):
        key = f'r:{i}'
        description = (text(graph, p, (RDFS.comment, SKOS.definition, RDFS.label)) or local_name(p)) + '\nSource: ' + str(p)
        model['relationships'][key] = {'type': rel_names[p], 'from': {'node': ids[a]}, 'to': {'node': ids[b]}, 'description': description}
        evidence['relationships'][key] = {'predicate': str(p), 'evidence': sorted(reasons)}
    axioms = {local_name(p): len(list(graph.triples((None, p, None)))) for p in
        (OWL.hasKey, OWL.disjointWith, OWL.unionOf, OWL.complementOf, OWL.propertyChainAxiom, OWL.inverseOf, OWL.imports)}
    report = {'profile': 'general-rdf', 'nodes': len(ids), 'relationships': len(edges), 'triples': len(graph),
              'source_metadata': evidence, 'warnings': dict(warnings), 'owl_restrictions': restrictions,
              'owl_axiom_counts': axioms,
              'owl_property_characteristics': {local_name(t): sorted(str(p) for p in graph.subjects(RDF.type, t)) for t in (OWL.FunctionalProperty, OWL.InverseFunctionalProperty, OWL.TransitiveProperty, OWL.SymmetricProperty)},
              'excluded_deprecated_classes': len(deprecated),
              'inferred_untyped_resources': len(untyped), 'limitations': [
                'Schema projection only. Record values are never exported. Observed signatures are evidence, not universal constraints.',
                'No OWL reasoning or import fetching. Equivalent-class conjunctions are projected in the forward direction only.',
                'OWL keys, cardinalities and functional properties are not automatically converted into database property constraints.',
                'Supported SHACL targetClass/simple property paths with minCount yield property EXISTS; other SHACL constructs are reported.',
                'Declared non-functional datatype properties use list types. Observed multiplicity is sample-dependent.',
                'Decimal maps to FLOAT and may lose precision. Unspecified dateTime timezone and unknown datatypes use ANY.',
                'No automatic taxonomy collapse for unknown ontologies: declared classes remain node types.']}
    if validate_model:
        if progress:
            progress(4, 'Validating schema and references')
        validate(model)
    return model, report


def extract_file(input_path, progress=None, reduce_to_root_concepts=False, prepared_input=None, root_scan=None):
    """Only required input: local filename/path. Return model, report and paths."""
    source = Path(input_path).expanduser().resolve(strict=True)
    if not isinstance(reduce_to_root_concepts, bool):
        raise ValueError('reduce_to_root_concepts must be True or False')
    if prepared_input is None:
        graph, input_metadata = rdf_loader.load_rdf_with_metadata(source, progress=progress)
    else:
        graph, input_metadata = prepared_input
        if input_metadata['path'] != str(source):
            raise ValueError('The input path changed; rerun the root-scan cell.')
    if reduce_to_root_concepts:
        from root_concepts import scan_root_concepts
        if root_scan is None:
            if progress:
                progress(3, 'Discovering root concepts')
            root_scan = scan_root_concepts(graph)
    if progress:
        progress(3, f'Extracting schema from {len(graph):,} triples')
    ontologies = sorted(graph.subjects(RDF.type, OWL.Ontology), key=str)
    name = next((text(graph, s, (RDFS.label, SKOS.prefLabel)) for s in ontologies
                 if text(graph, s, (RDFS.label, SKOS.prefLabel))), source.name.removesuffix('.gz').rsplit('.', 1)[0])
    model, report = general_schema(graph, name, progress=progress, validate_model=not reduce_to_root_concepts)
    if reduce_to_root_concepts:
        from root_concepts import reduce_to_roots
        if progress:
            progress(3, 'Reducing schema to root concepts')
        model, report = reduce_to_roots(graph, model, report, root_scan, progress=progress)
    report['input'] = input_metadata
    report['nodes'] = len(model['nodes']); report['relationships'] = len(model['relationships'])
    stem = source.name[:-3] if source.name.lower().endswith('.gz') else source.name
    stem = Path(stem).stem
    # Absolute-path digest avoids clobbering outputs from different same-named files.
    digest = hashlib.sha256(str(source).encode()).hexdigest()[:8]
    mode_suffix = '.reduced' if reduce_to_root_concepts else ''
    output = Path(__file__).parent / 'output' / f'{stem}-{digest}{mode_suffix}.ontology.json'
    if progress:
        progress(5, f'Saving {len(model["nodes"]):,} node types and {len(model["relationships"]):,} relationship definitions')
    output, report_path = write_output(model, report, output)
    if progress:
        progress(6, f'Saved upload JSON and report: {output.name}')
    return model, report, output, report_path
