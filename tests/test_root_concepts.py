import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from rdflib import Graph
from root_concepts import scan_root_concepts, reduce_to_roots
from rdf_schema import general_schema, extract_file
from ontology_converter import validate

TTL = '''
@prefix ex: <https://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Root a owl:Class; rdfs:label "root concept" .
ex:Child a owl:Class; rdfs:subClassOf ex:Root; rdfs:label "a child";
 rdfs:subClassOf [owl:onProperty ex:part; owl:someValuesFrom ex:Orphan] .
ex:Orphan a owl:Class; rdfs:label "orphan" .
ex:part a owl:ObjectProperty; rdfs:label "part of" .
'''

class RootReductionTests(unittest.TestCase):
    def test_roots_camelcase_orphans_and_collapsed_relationships(self):
        g=Graph().parse(data=TTL,format='turtle')
        scan=scan_root_concepts(g)
        self.assertEqual(scan['classes_scanned'], 3)
        self.assertEqual(scan['active_classes_scanned'], 3)
        self.assertEqual(scan['triples_scanned'], len(g))
        self.assertEqual(scan['roots'], [{'iri':'https://example.org/Root','label':'RootConcept'}])
        self.assertEqual(scan['orphan_classes'], ['https://example.org/Orphan'])
        m,r=general_schema(g,'test');m,r=reduce_to_roots(g,m,r,scan)
        labels={k:n['labels']['identifier'] for k,n in m['nodes'].items()}
        self.assertEqual(set(labels.values()),{'RootConcept','ExternalConcepts'})
        self.assertIn(('RootConcept','PART_OF','ExternalConcepts'), {(labels[x['from']['node']],x['type'],labels[x['to']['node']]) for x in m['relationships'].values()})
        self.assertTrue(any(n['properties'] for n in m['nodes'].values()))
        self.assertTrue(validate(m))

    def test_audit_class_count_includes_deprecated_classes_before_exclusion(self):
        g=Graph().parse(data=TTL+'\nex:Old a owl:Class; owl:deprecated true .',format='turtle')
        scan=scan_root_concepts(g)
        self.assertEqual(scan['classes_scanned'],4)
        self.assertEqual(scan['active_classes_scanned'],3)
        self.assertEqual(scan['excluded_classes'],1)

    def test_declared_roots_take_precedence_over_upper_ontology(self):
        g=Graph().parse(data=TTL+'''
@prefix iao: <http://purl.obolibrary.org/obo/> .
ex:ontology a owl:Ontology; iao:IAO_0000700 ex:Root .
ex:Top a owl:Class . ex:Root rdfs:subClassOf ex:Top .
''',format='turtle')
        scan=scan_root_concepts(g)
        self.assertEqual(scan['root_source'],'declared ontology roots')
        self.assertEqual(scan['roots'][0]['label'],'RootConcept')
        self.assertIn('https://example.org/Top',scan['orphan_classes'])

    def test_multiple_inheritance_rootless_cycle_and_isolated_classes(self):
        g=Graph().parse(data=TTL+'''
ex:Second a owl:Class . ex:Child rdfs:subClassOf ex:Second .
ex:C1 a owl:Class; rdfs:subClassOf ex:C2 .
ex:C2 a owl:Class; rdfs:subClassOf ex:C1 .
''',format='turtle')
        scan=scan_root_concepts(g)
        self.assertEqual(len(scan['memberships']['https://example.org/Child']),2)
        self.assertIn('https://example.org/C1',scan['orphan_classes'])
        self.assertIn('https://example.org/C2',scan['orphan_classes'])

    def test_disabled_mode_skips_scan_and_preserves_existing_output(self):
        import rdf_schema
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'test.ttl';p.write_text(TTL)
            with patch.object(rdf_schema,'__file__',str(Path(d)/'rdf_schema.py')):
                normal,_,normal_path,_=extract_file(p)
                with patch('root_concepts.scan_root_concepts',side_effect=AssertionError('scan must be skipped')):
                    disabled,_,_,_=extract_file(p,reduce_to_root_concepts=False)
                self.assertEqual(normal,disabled)
                reduced,report,reduced_path,_=extract_file(p,reduce_to_root_concepts=True)
                self.assertNotEqual(normal_path,reduced_path)
                self.assertTrue(reduced_path.name.endswith('.reduced.ontology.json'))
                self.assertTrue(validate(reduced))
                self.assertEqual(report['nodes'],2)
                sidecar=json.loads(reduced_path.with_suffix('.report.json').read_text())
                self.assertEqual(sidecar['root_concepts']['classes_scanned'],3)
                self.assertEqual(json.loads(normal_path.read_text()),normal)
