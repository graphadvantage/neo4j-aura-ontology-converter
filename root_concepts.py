"""Domain-independent reduction using the asserted named subclass hierarchy."""
import hashlib
import re
from collections import Counter, defaultdict, deque
from rdflib import URIRef, Literal, RDF, RDFS, OWL, SKOS
from ontology_converter import identifier, local_name, text, validate


def camel_case(value):
    parts = re.findall(r'[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+', value.replace('_', ' '))
    return identifier(''.join(p[:1].upper() + p[1:].lower() for p in parts) or value)


def format_audit_count(count):
    """Compact magnitude plus the exact count for reproducible audit summaries."""
    for scale, suffix in ((1_000_000_000, 'B'), (1_000_000, 'M'), (1_000, 'K')):
        if count >= scale:
            compact = f'{count / scale:.1f}'.rstrip('0').rstrip('.')
            return f'{compact}{suffix} ({count:,})'
    return f'{count:,}'


def scan_root_concepts(graph):
    classes = {s for t in (OWL.Class, RDFS.Class) for s in graph.subjects(RDF.type, t) if isinstance(s, URIRef)}
    hierarchy = [(s, o) for s, o in graph.subject_objects(RDFS.subClassOf)
                 if isinstance(s, URIRef) and isinstance(o, URIRef)]
    classes.update(c for pair in hierarchy for c in pair)
    classes_scanned = len(classes)
    excluded = {OWL.Thing, OWL.Nothing, RDFS.Resource}
    excluded.update(c for c in classes if any(str(v).lower() in ('true', '1') for v in graph.objects(c, OWL.deprecated)))
    classes -= excluded
    parents, children = defaultdict(set), defaultdict(set)
    for child, parent in hierarchy:
        if child in classes and parent in classes and child != parent:
            parents[child].add(parent); children[parent].add(child)
    root_predicate = URIRef('http://purl.obolibrary.org/obo/IAO_0000700')
    declared = {r for ontology in graph.subjects(RDF.type, OWL.Ontology)
                for r in graph.objects(ontology, root_predicate) if r in classes}
    roots = sorted(declared or {c for c in classes if not parents[c] and children[c]}, key=str)
    labels = {r: camel_case(text(graph, r, (RDFS.label, SKOS.prefLabel)) or local_name(r)) for r in roots}
    duplicates = Counter(labels.values()); used = {'ExternalConcepts'}
    for r in roots:
        label = labels[r]
        if duplicates[label] > 1 or label in used:
            label += hashlib.sha256(str(r).encode()).hexdigest()[:8].upper()
        while label in used: label += 'X'
        labels[r] = label; used.add(label)
    memberships = defaultdict(set)
    for root in roots:
        queue, seen = deque([root]), {root}
        while queue:
            c = queue.popleft(); memberships[str(c)].add(str(root))
            for child in children[c]:
                if child not in seen: seen.add(child); queue.append(child)
    return {'classes_scanned': classes_scanned,
            'active_classes_scanned': len(classes),
            'excluded_classes': classes_scanned - len(classes),
            'triples_scanned': len(graph),
            'class_count_basis': 'Distinct named OWL/RDFS class declarations and named subclass endpoints, before deprecated/universal-class exclusions; not RDF triples or instance records.',
            'root_source': 'declared ontology roots' if declared else 'named subclass hierarchy',
            'roots': [{'iri': str(r), 'label': labels[r]} for r in roots],
            'memberships': {c: sorted(v) for c, v in sorted(memberships.items())},
            'orphan_classes': sorted(str(c) for c in classes if not memberships[str(c)]),
            'multiple_root_classes': sorted(c for c, v in memberships.items() if len(v) > 1),
            'policy': 'Use IAO has ontology root term annotations when present; otherwise roots have named subclasses and no named superclass in the active hierarchy. Universal OWL/RDFS tops are ignored. Isolated classes and rootless cycles are external. Only asserted named rdfs:subClassOf is used; no OWL reasoning.'}


def reduce_to_roots(graph, model, report, scan, progress=None):
    source_nodes = report['source_metadata']['nodes']
    memberships = scan['memberships']
    roots = scan['roots']
    group_keys = [r['iri'] for r in roots]
    member_groups = {key: memberships.get(uri, []) or ['ExternalConcepts'] for key, uri in source_nodes.items()}
    if any('ExternalConcepts' in groups for groups in member_groups.values()):
        group_keys.append('ExternalConcepts')
    ids = {root: f'n:{i}' for i, root in enumerate(group_keys)}
    labels = {r['iri']: r['label'] for r in roots}; labels['ExternalConcepts'] = 'ExternalConcepts'
    reduced = {k: v for k, v in model.items() if k not in ('nodes', 'relationships')}
    reduced['description'] += '\nRoot-concept reduction: category-level patterns, not assertions that root classes relate to themselves. Multiple inheritance can assign a class to multiple categories.'
    reduced['nodes'] = {}; reduced['relationships'] = {}
    grouped_members = defaultdict(set)
    for node, groups in member_groups.items():
        for group in groups: grouped_members[group].add(source_nodes[node])
    for i, root in enumerate(group_keys):
        reduced['nodes'][ids[root]] = {'labels': {'identifier': labels[root]},
            'description': ('Classes with no path to a discovered root.' if root == 'ExternalConcepts' else 'Root concept: ' + root) + f' Groups {len(grouped_members[root]):,} source classes.',
            'properties': {}, 'constraints': {}, 'extensions': {'display': {'x': i % 5 * 400, 'y': i // 5 * 300}}}
    properties = defaultdict(list)
    for key, node in model['nodes'].items():
        for prop_id, prop in node['properties'].items():
            predicate = report['source_metadata']['properties'][prop_id]['predicate']
            for root in member_groups[key]: properties[(root, predicate)].append(prop)
    # Discover metadata on grouped classes generically, without a vocabulary whitelist.
    observations = defaultdict(lambda: {'types': set(), 'counts': Counter()})
    from rdf_schema import literal_type, unique_names
    class_groups = {uri: member_groups[key] for key, uri in source_nodes.items()}
    for uri, groups in class_groups.items():
        for predicate, value in graph.predicate_objects(URIRef(uri)):
            if isinstance(value, Literal) and predicate != OWL.deprecated:
                for root in groups:
                    stat = observations[(root, str(predicate))]
                    stat['types'].add(literal_type(value)); stat['counts'][uri] += 1
    predicate_names = unique_names({URIRef(p) for _, p in set(properties) | set(observations)}, graph)
    for key, info in observations.items():
        dtype = next(iter(info['types'])) if len(info['types']) == 1 else 'ANY'
        if max(info['counts'].values()) > 1: dtype = f'LIST<{dtype}>'
        properties[key].append({'name': predicate_names[URIRef(key[1])], 'type': dtype,
            'description': 'Metadata on source classes. Source: ' + key[1]})
    source_metadata = {'nodes': {ids[r]: r for r in group_keys}, 'properties': {}, 'relationships': {}}
    for i, ((root, predicate), props) in enumerate(sorted(properties.items())):
        types = {p['type'] for p in props}; list_value = any(t.startswith('LIST<') for t in types)
        base_types = {t[5:-1] if t.startswith('LIST<') else t for t in types}
        dtype = next(iter(base_types)) if len(base_types) == 1 else 'ANY'
        if list_value: dtype = f'LIST<{dtype}>'
        prop = {'name': predicate_names[URIRef(predicate)], 'type': dtype,
                'description': '\n'.join(sorted({p['description'] for p in props}))}
        key = f'p:{i}'; reduced['nodes'][ids[root]]['properties'][key] = prop
        source_metadata['properties'][key] = {'predicate': predicate, 'owner': root, 'evidence': ['root-group projection']}
    signatures = defaultdict(set)
    for key, rel in model['relationships'].items():
        for source in member_groups[rel['from']['node']]:
            for target in member_groups[rel['to']['node']]:
                signatures[(source, rel['type'], target)].add(key)
    for i, ((source, kind, target), evidence) in enumerate(sorted(signatures.items())):
        key = f'r:{i}'
        reduced['relationships'][key] = {'type': kind, 'from': {'node': ids[source]}, 'to': {'node': ids[target]},
            'description': f'Category-level projection of {len(evidence):,} source relationship definitions; not a biological-instance or root self-relationship assertion.'}
        source_metadata['relationships'][key] = {'evidence_count': len(evidence)}
    report['unreduced_counts'] = {'nodes': len(model['nodes']), 'relationships': len(model['relationships'])}
    report['constraints_omitted_by_reduction'] = sum(len(n['constraints']) for n in model['nodes'].values())
    report['root_concepts'] = scan
    report['root_concepts']['orphan_classes'] = sorted(uri for uri, groups in class_groups.items() if groups == ['ExternalConcepts'])
    report['source_metadata'] = source_metadata
    report['profile'] = 'general-rdf-root-concepts'
    report['limitations'].append('Grouping broadens allowed signatures. Class-level constraints are not promoted to root-wide constraints; metadata properties are inferred, not mandatory.')
    if progress:
        progress(4, 'Validating reduced schema and references')
    validate(reduced)
    return reduced, report
