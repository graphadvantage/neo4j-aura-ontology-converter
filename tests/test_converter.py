import copy
import unittest
from rdflib import Graph
from ontology_converter import convert, validate

TTL = '''
@prefix ex: <https://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:A a owl:Class; rdfs:label "Alpha"; rdfs:subClassOf ex:B,
 [a owl:Restriction; owl:onProperty ex:partOf; owl:someValuesFrom ex:B],
 [a owl:Restriction; owl:onProperty ex:partOf; owl:allValuesFrom ex:B] .
ex:B a owl:Class .
ex:Old a owl:Class; owl:deprecated true .
ex:partOf a owl:ObjectProperty; rdfs:label "part of"; rdfs:domain ex:A; rdfs:range ex:B .
ex:age a owl:DatatypeProperty, owl:FunctionalProperty; rdfs:domain ex:A; rdfs:range xsd:integer .
ex:title a owl:DatatypeProperty; rdfs:domain ex:A; rdfs:range xsd:string .
'''

class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph().parse(data=TTL, format='turtle')

    def test_projection_and_report(self):
        model, report = convert(self.graph)
        self.assertTrue(validate(model))
        self.assertEqual(len(model['nodes']), 2)
        self.assertEqual(len(model['relationships']), 3)
        self.assertEqual({r['type'] for r in model['relationships'].values()}, {'IS_A', 'PART_OF'})
        node = next(n for n in model['nodes'].values() if n['labels']['identifier'] == 'A')
        self.assertIn('Alpha', node['description'])
        self.assertEqual({p['type'] for p in node['properties'].values()}, {'INTEGER', 'LIST<STRING>'})
        self.assertEqual(report['counts']['unsupported_subclass_expressions'], 1)
        self.assertEqual(report['counts']['deprecated_classes_excluded'], 1)
        self.assertEqual(convert(self.graph)[0], model)

    def test_subset_and_dangling_reference(self):
        model, report = convert(self.graph, class_iris=['https://example.org/A'])
        self.assertTrue(validate(model))
        self.assertEqual(len(model['relationships']), 0)
        self.assertGreater(report['counts']['relationships_with_excluded_or_unknown_endpoints'], 0)
        broken = copy.deepcopy(model)
        broken['relationships']['r:0'] = {'type': 'BAD', 'from': {'node': 'missing'}, 'to': {'node': 'n:0'}}
        with self.assertRaises(ValueError):
            validate(broken)

    def test_aura_roundtrip_profile(self):
        from pathlib import Path
        import json
        model, _ = convert(Graph().parse(Path(__file__).resolve().parents[1] / 'examples' / 'transport.owl'), name='Transport Ontology')
        roundtrip = json.loads((Path(__file__).resolve().parents[1] / 'examples' / 'Transport-Ontology.json').read_text())
        for key, expected in roundtrip['nodes'].items():
            node = model['nodes'][key]
            self.assertEqual(node['labels'], expected['labels'])
            self.assertEqual(node['description'], expected['description'])
            self.assertEqual(set(node), {'labels', 'description', 'properties', 'constraints', 'extensions'})
        self.assertEqual(model['relationships'], roundtrip['relationships'])
        self.assertTrue(validate(model))

    def test_colliding_local_names(self):
        self.graph.parse(data='<https://other.org/A> a <http://www.w3.org/2002/07/owl#Class> .', format='turtle')
        model, _ = convert(self.graph)
        labels = [n['labels']['identifier'] for n in model['nodes'].values()]
        self.assertEqual(len(labels), len(set(labels)))

if __name__ == '__main__':
    unittest.main()
