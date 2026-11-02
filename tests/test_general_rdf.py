import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rdflib import Graph, Dataset, URIRef
from rdflib.compare import isomorphic
from rdf_loader import load_rdf
from rdf_schema import extract_file, general_schema
from ontology_converter import validate

SCHEMA = '''
@prefix ex: <https://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:Person a owl:Class . ex:Team a owl:Class .
ex:age a owl:DatatypeProperty, owl:FunctionalProperty;
 rdfs:domain ex:Person; rdfs:range xsd:unsignedInt; rdfs:comment "Age in years." .
ex:member a owl:ObjectProperty; rdfs:domain ex:Person; rdfs:range ex:Team .
ex:p a ex:Person; ex:age 42; ex:member ex:t; ex:tag "one", "two" .
ex:t a ex:Team .
'''

class RdfFormatTests(unittest.TestCase):
    def test_serializations_have_identical_schema_and_named_graph_union(self):
        source = Graph().parse(data=SCHEMA, format='turtle')
        expected, _ = general_schema(source, 'test')
        extensions = {'xml': 'rdf', 'turtle': 'ttl', 'nt': 'nt', 'n3': 'n3',
                      'json-ld': 'jsonld', 'trig': 'trig', 'nquads': 'nq', 'trix': 'trix'}
        with tempfile.TemporaryDirectory() as directory:
            for fmt, extension in extensions.items():
                with self.subTest(format=fmt):
                    path = Path(directory) / f'test.{extension}'
                    if fmt in ('trig', 'nquads', 'trix'):
                        dataset = Dataset()
                        # Divide declarations and observations across graphs.
                        contexts = [dataset.graph(URIRef('https://example.org/g1')),
                                    dataset.graph(URIRef('https://example.org/g2'))]
                        for i, triple in enumerate(sorted(source, key=str)):
                            contexts[i % 2].add(triple)
                        dataset.serialize(path, format=fmt)
                    else:
                        source.serialize(path, format=fmt)
                    loaded = load_rdf(path)
                    self.assertTrue(isomorphic(source, loaded))
                    model, report = general_schema(loaded, 'test')
                    self.assertEqual(model, expected)
                    self.assertTrue(validate(model))
                    if fmt in ('trig', 'nquads', 'trix'):
                        self.assertEqual(loaded.source_metadata['nonempty_graphs'], 2)

    def test_misnamed_unknown_and_gzip_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('turtle.owl', 'unknown.data', 'compressed.ttl.gz'):
                path = Path(directory) / name
                if name.endswith('.gz'):
                    with gzip.open(path, 'wt') as stream: stream.write(SCHEMA)
                else:
                    path.write_text(SCHEMA)
                self.assertTrue(isomorphic(load_rdf(path), Graph().parse(data=SCHEMA, format='turtle')))

    def test_jsonld_named_graphs_are_not_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'named.jsonld'
            path.write_text(json.dumps({'@context': {'ex': 'https://example.org/'},
                '@id': 'ex:g', '@graph': [{'@id': 'ex:p', '@type': 'ex:Person', 'ex:name': 'Pat'}]}))
            graph = load_rdf(path)
            self.assertEqual(len(graph), 2)
            model, _ = general_schema(graph, 'test')
            self.assertEqual(len(model['nodes']), 1)

    def test_invalid_empty_remote_context_and_unsupported_owl_fail_clearly(self):
        values = {'owlxml.owl': '<Ontology xmlns="http://www.w3.org/2002/07/owl#"><Declaration><Class IRI="https://example.org/A"/></Declaration></Ontology>', 'empty.rdf': '', 'bad.ttl': 'this is not valid RDF',
                  'remote.jsonld': '{"@context":"https://example.org/context.jsonld"}',
                  'functional.owl': 'Ontology(<https://example.org/> Declaration(Class(<https://example.org/A>)))'}
        with tempfile.TemporaryDirectory() as directory:
            for name, value in values.items():
                with self.subTest(name=name):
                    path = Path(directory) / name; path.write_text(value)
                    with self.assertRaises(ValueError): load_rdf(path)

class GeneralSchemaTests(unittest.TestCase):
    def test_declared_and_observed_properties_and_no_invented_constraints(self):
        model, report = general_schema(Graph().parse(data=SCHEMA, format='turtle'), 'test')
        self.assertEqual(len(model['nodes']), 2)
        person = next(n for n in model['nodes'].values() if n['labels']['identifier'] == 'Person')
        props = {p['name']: p for p in person['properties'].values()}
        self.assertEqual(props['age']['type'], 'INTEGER')
        self.assertEqual(props['tag']['type'], 'LIST<STRING>')
        self.assertEqual(person['constraints'], {})
        self.assertEqual(len(model['relationships']), 1)  # deduplicate observed/declared signature
        self.assertIn('member', next(iter(report['source_metadata']['relationships'].values()))['predicate'])

    def test_data_only_skos_and_untyped_inputs(self):
        cases = [('''@prefix ex: <https://example.org/> . ex:p a ex:Person; ex:name "Pat".''', 'Person'),
                 ('''@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
                     <https://example.org/c> a skos:Concept; skos:prefLabel "one".''', 'Concept'),
                 ('''<https://example.org/p> <https://example.org/name> "Pat" .''', 'Resource')]
        for data, expected in cases:
            with self.subTest(expected=expected):
                model, _ = general_schema(Graph().parse(data=data, format='turtle'), 'test')
                self.assertEqual([n['labels']['identifier'] for n in model['nodes'].values()], [expected])
                self.assertTrue(next(iter(model['nodes'].values()))['properties'])

    def test_shacl_simple_existence_and_complex_path_reporting(self):
        data = '''@prefix ex: <https://example.org/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:shape a sh:NodeShape; sh:targetClass ex:Person;
 sh:property [sh:path ex:name; sh:datatype xsd:string; sh:minCount 1; sh:maxCount 1],
 [sh:path [sh:inversePath ex:other]; sh:minCount 1] .'''
        model, report = general_schema(Graph().parse(data=data, format='turtle'), 'test')
        node = next(iter(model['nodes'].values()))
        self.assertEqual(len(node['constraints']), 1)
        self.assertEqual(next(iter(node['constraints'].values()))['type'], 'EXISTS')
        self.assertEqual(next(iter(node['properties'].values()))['type'], 'STRING')
        self.assertEqual(report['warnings']['unsupported_shacl_path'], 1)
        self.assertTrue(validate(model))

    def test_nested_owl_undeclared_fillers_and_collision_safe_names(self):
        data = '''@prefix ex: <https://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:A a owl:Class; owl:equivalentClass [owl:intersectionOf (ex:Base
 [owl:onProperty ex:part; owl:someValuesFrom ex:B]
 [owl:onProperty ex:part; owl:allValuesFrom ex:B])] .
ex:part rdfs:label "part of" .
<https://other.org/A> a owl:Class .'''
        model, report = general_schema(Graph().parse(data=data, format='turtle'), 'test')
        self.assertEqual(len(model['nodes']), 4)
        names = [n['labels']['identifier'] for n in model['nodes'].values()]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual({r['type'] for r in model['relationships'].values()}, {'IS_A', 'PART_OF'})
        self.assertEqual(len(report['owl_restrictions']), 1)

    def test_predicate_descriptions_are_not_inferred_as_data_records(self):
        data = SCHEMA + '\n<https://example.org/tag> <http://www.w3.org/2000/01/rdf-schema#label> "Tag" .'
        model, _ = general_schema(Graph().parse(data=data, format='turtle'), 'test')
        self.assertEqual(len(model['nodes']), 2)

    def test_deactivated_shacl_does_not_create_constraints(self):
        data = '''@prefix ex: <https://example.org/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:shape a sh:NodeShape; sh:targetClass ex:Person; sh:deactivated true;
 sh:property [sh:path ex:name; sh:datatype xsd:string; sh:minCount 1] .'''
        model, _ = general_schema(Graph().parse(data=data, format='turtle'), 'test')
        self.assertTrue(all(not n['constraints'] for n in model['nodes'].values()))

    def test_pipeline_recreates_deleted_outputs_without_graph_attributes(self):
        import shutil
        import rdf_loader
        import rdf_schema
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'schema.ttl'
            source.write_text(SCHEMA)
            graph, metadata = rdf_loader.load_rdf_with_metadata(source)
            self.assertFalse(hasattr(graph, 'source_metadata'))
            self.assertEqual(metadata['format'], 'turtle')
            with patch.object(rdf_schema, '__file__', str(Path(directory) / 'rdf_schema.py')):
                _, report, output, sidecar = extract_file(source)
                self.assertTrue(output.exists())
                shutil.rmtree(output.parent)
                self.assertFalse(output.parent.exists())
                stages = []
                model, report, output, sidecar = extract_file(source, progress=lambda stage, message: stages.append(stage))
                self.assertEqual(sorted(set(stages)), [1, 2, 3, 4, 5, 6])
                self.assertTrue(output.exists())
                self.assertTrue(sidecar.exists())
                self.assertEqual(report['input']['format'], 'turtle')
                self.assertTrue(validate(model))

    def test_single_file_pipeline_has_no_go_specific_grouping(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'schema.ttl'; source.write_text(SCHEMA)
            # Keep generated artifacts inside the temporary workspace.
            import rdf_schema
            with patch.object(rdf_schema, '__file__', str(Path(directory) / 'rdf_schema.py')):
                model, report, output, sidecar = extract_file(source)
            self.assertTrue(output.exists()); self.assertTrue(sidecar.exists())
            self.assertEqual(model['name'], 'schema')
            self.assertEqual(report['input']['format'], 'turtle')
            self.assertTrue(validate(json.loads(output.read_text())))
            go = Path(directory) / 'not-named-go.ttl'
            go.write_text('''@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix oio: <http://www.geneontology.org/formats/oboInOwl#> .
<https://example.org/bp> a owl:Class; oio:hasOBONamespace "biological_process" .
<https://example.org/mf> a owl:Class; oio:hasOBONamespace "molecular_function" .
<https://example.org/cc> a owl:Class; oio:hasOBONamespace "cellular_component" .''')
            with patch.object(rdf_schema, '__file__', str(Path(directory) / 'rdf_schema.py')):
                model, report, _, _ = extract_file(go)
            self.assertEqual(report['profile'], 'general-rdf')
            self.assertEqual(len(model['nodes']), 3)
            self.assertEqual({n['labels']['identifier'] for n in model['nodes'].values()}, {'bp', 'mf', 'cc'})
            self.assertTrue(all(not n['properties'] for n in model['nodes'].values()))
