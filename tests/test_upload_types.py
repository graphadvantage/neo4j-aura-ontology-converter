import json
import tempfile
import unittest
from pathlib import Path
from ontology_converter import write_output

class UploadTypeTests(unittest.TestCase):
    def test_ambiguous_types_are_normalized_and_recorded_for_nodes_and_relationships(self):
        model = {'schema': 'https://neo4j.com/docs/ontology-graph-spec/1.0.0/schema.json', 'version':'1', 'id':'test',
         'nodes': {'n:0': {'labels': {'identifier':'Test'}, 'properties':{
          'p:0': {'name':'mixed', 'type':'ANY'},
          'p:1': {'name':'items', 'type':'LIST<ANY>'},
          'p:2': {'name':'count', 'type':'INTEGER'}}}},
         'relationships': {'r:0': {'type':'LINK', 'from':{'node':'n:0'}, 'to':{'node':'n:0'},
          'properties':{'p:3':{'name':'other','type':'ANY'}}}}}
        report = {}
        with tempfile.TemporaryDirectory() as d:
            output, sidecar = write_output(model, report, Path(d)/'test.json')
            written = json.loads(output.read_text())
            props=written['nodes']['n:0']['properties']
            self.assertEqual([props[f'p:{i}']['type'] for i in range(3)],['STRING','LIST<STRING>','INTEGER'])
            self.assertEqual(written['relationships']['r:0']['properties']['p:3']['type'],'STRING')
            self.assertIn('stringify',props['p:0']['description'])
            self.assertEqual(len(json.loads(sidecar.read_text())['upload_type_coercions']),3)
            # Writing the same schema again must not duplicate compatibility notes.
            write_output(model,report,output)
            self.assertEqual(len(report['upload_type_coercions']),3)
