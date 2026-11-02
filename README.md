# RDF / OWL to Neo4j Aura Ontology

Convert a local RDF file into an uploadable Aura ontology JSON. The notebook
extracts node types, property definitions, and relationship patterns; importing
individual records and values into Neo4j is a separate step.

## Run

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Open `rdf_to_aura_ontology.ipynb`, select the `.venv` Python kernel, and set:

```python
INPUT_PATH = "examples/transport.owl"
REDUCE_TO_ROOT_CONCEPTS = False
```

Choose **Run All**. Paths may be absolute or relative to this repository. Start
the notebook from the repository directory or a subdirectory. Format detection,
model naming, validation, and output filenames are automatic. A live stage bar
and elapsed timer show processing progress. No database credentials are needed.

Upload the generated **`.ontology.json`** file in Aura. The companion
**`.ontology.report.json`** contains mappings, audit counts, warnings, and limits.
Outputs are created in `output/`, including when that directory has been deleted.

| Mode | Upload filename |
| --- | --- |
| Full schema | `<input>-<path-hash>.ontology.json` |
| Root reduction | `<input>-<path-hash>.reduced.ontology.json` |

The path hash distinguishes identically named inputs in different directories.
Rerunning the same input and mode replaces its outputs. Input files are unchanged.

## Root-concept reduction

Set `REDUCE_TO_ROOT_CONCEPTS = True` for a compact category model. A separate scan
cell lists the discovered roots as CamelCase node labels before conversion.
It also reports compact and exact counts of named classes scanned, active and
excluded classes, RDF triples, orphan classes, and multiple-root memberships.
The audit counts are saved under `root_concepts` in the report.

Explicit ontology roots declared through `IAO:0000700` (“has ontology root term”)
take priority. Otherwise roots are active named classes with descendants and no
named superclass in the asserted `rdfs:subClassOf` hierarchy. Universal OWL/RDFS
top classes are ignored.

Descendants are grouped under reachable roots. Isolated classes and rootless
cycles go into **ExternalConcepts**. Classes reaching multiple roots belong to
all of them. Relationship patterns and properties are combined per category;
metadata on class records also supplies property definitions. Constraints on
individual classes are not promoted to entire groups. A category self-loop
represents relationships between member terms, not a root relating to itself.

With `False`, root scanning is skipped and classes remain separate node types.
Both modes use domain-independent rules, without input-specific class IDs or
relationship whitelists.

## Supported formats

| Format | Extensions | Transport example |
| --- | --- | --- |
| RDF/XML | `.rdf`, `.xml`, `.owl` | [transport.owl](examples/transport.owl) |
| Turtle | `.ttl` | [transport.ttl](examples/transport.ttl) |
| JSON-LD | `.jsonld`, `.json-ld`, `.json` | [transport.jsonld](examples/transport.jsonld) |
| N-Triples | `.nt` | [transport.nt](examples/transport.nt) |
| N-Quads | `.nq`, `.nquads` | [transport.nq](examples/transport.nq) |
| TriG | `.trig` | [transport.trig](examples/transport.trig) |
| N3 | `.n3` | [transport.n3](examples/transport.n3) |
| TriX | `.trix` | [transport.trix](examples/transport.trix) |

All eight formats support gzip-compressed inputs (`.gz`). Content detection and
parser fallback handle misleading extensions: `.owl` does not specify a syntax.
HDT, OWL/XML, and OWL Functional Syntax are not supported. JSON-LD contexts must
be embedded; external contexts and OWL imports are not fetched. Named graphs are
combined into a union for schema extraction, without retaining graph provenance.

The Transport examples each contain the same 22 triples and produce five node
types and four relationships with reduction disabled. Dataset examples use the
named graph `urn:transport:example`. `examples/Transport-Ontology.json` is the
Aura roundtrip reference used by tests.

For a larger input, download an OWL file from the
[GO Consortium](https://geneontology.org/docs/download-ontology/) into `examples/`
and set `INPUT_PATH` accordingly. GO-plus declares three GO aspect roots;
reduction groups them as MolecularFunction, CellularComponent, and
BiologicalProcess, with unrooted classes in ExternalConcepts. The large GO-plus
input and generated outputs are excluded from Git. Large files require
substantially more memory than their disk size.

## Extraction limits

OWL/RDFS declarations supply classes, domain/range signatures, datatype
properties, subclass links, and supported existential restrictions. Conjunctions
in equivalent-class definitions are projected in the forward direction. Typed
RDF records can supply inferred property and relationship signatures; untyped
data uses a Resource category. No individual record values are exported.

Supported SHACL target classes and simple literal-property paths can supply
existence constraints. OWL keys, cardinalities, functional properties, and
unsupported SHACL constructs are documented rather than treated as equivalent
Neo4j property constraints. Observed completeness does not imply existence.

Ambiguous types are exported as STRING or LIST<STRING> for Aura compatibility,
with each fallback recorded in the report. A data importer must stringify those
values. Mapping decimal values to FLOAT can lose precision. This is a lossy
schema projection with no OWL reasoning; local schema validation does not verify
Aura upload acceptance or editor limits.

## Implementation and checks

- `rdf_loader.py`: serialization detection, decompression, and named-graph union.
- `rdf_schema.py`: general extraction and output pipeline.
- `root_concepts.py`: root discovery, audit counts, and optional reduction.
- `extraction_progress.py`: notebook stage indicator and elapsed timer.
- `ontology_converter.py`: shared validation, upload-type normalization, and class projection.
- `schemas/`: bundled Neo4j Ontology Graph Specification 1.0.0 schema.

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover the eight formats, compression, named graphs, format detection,
OWL/RDFS extraction, RDF records, SKOS, SHACL, root reduction, audit counts,
output recreation, upload-type fallbacks, and the progress indicator.
`requirements-lock.txt` records the dependency versions used during development.
