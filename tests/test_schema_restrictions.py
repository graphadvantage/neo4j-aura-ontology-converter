import unittest
from rdflib import Graph
from rdf_schema import general_schema
from ontology_converter import validate


class SchemaRestrictionTests(unittest.TestCase):
    def test_class_relationships_and_cardinalities_are_not_grouped_by_vocabulary(self):
        ttl = '''
@prefix ex: <https://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix oio: <http://www.geneontology.org/formats/oboInOwl#> .
ex:mf a owl:Class; oio:hasOBONamespace "molecular_function";
 owl:equivalentClass [owl:intersectionOf (ex:base [owl:onProperty ex:part; owl:someValuesFrom ex:bp])] .
ex:base a owl:Class; oio:hasOBONamespace "molecular_function" .
ex:bp a owl:Class; oio:hasOBONamespace "biological_process" .
ex:external a owl:Class; rdfs:subClassOf [owl:onProperty ex:part; owl:minQualifiedCardinality 2; owl:onClass ex:bp] .
ex:part a owl:ObjectProperty, owl:FunctionalProperty; rdfs:label "part of" .
'''
        model, report = general_schema(Graph().parse(data=ttl, format='turtle'), 'test')
        labels = {key: node['labels']['identifier'] for key, node in model['nodes'].items()}
        self.assertEqual(set(labels.values()), {'mf', 'base', 'bp', 'external'})
        relations = {(labels[r['from']['node']], r['type'], labels[r['to']['node']]) for r in model['relationships'].values()}
        self.assertIn(('mf', 'PART_OF', 'bp'), relations)
        self.assertIn(('mf', 'IS_A', 'base'), relations)
        self.assertEqual(len(report['owl_restrictions']), 1)
        self.assertEqual(report['owl_restrictions'][0]['value'], '2')
        self.assertEqual(report['owl_property_characteristics']['FunctionalProperty'], ['https://example.org/part'])
        self.assertTrue(all(not n['constraints'] for n in model['nodes'].values()))
        self.assertTrue(validate(model))
