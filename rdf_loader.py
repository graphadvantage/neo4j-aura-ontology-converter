"""Local RDF serialization detection with named-graph union and gzip support."""
import gzip
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from rdflib import Dataset, Graph

FORMATS = ('xml', 'turtle', 'nt', 'n3', 'json-ld', 'trig', 'nquads', 'trix')
EXTENSIONS = {'.owl': 'xml', '.rdf': 'xml', '.xml': 'xml', '.ttl': 'turtle',
              '.nt': 'nt', '.n3': 'n3', '.json': 'json-ld', '.jsonld': 'json-ld',
              '.json-ld': 'json-ld', '.trig': 'trig', '.nq': 'nquads',
              '.nquads': 'nquads', '.trix': 'trix'}


def _local_json_contexts(value):
    """Do not let JSON-LD implicitly depend on a remote context document."""
    if isinstance(value, dict):
        if '@import' in value:
            raise ValueError('JSON-LD @import contexts are not supported; embed the context locally.')
        if '@context' in value:
            contexts = value['@context']
            for item in contexts if isinstance(contexts, list) else [contexts]:
                if isinstance(item, str):
                    raise ValueError('JSON-LD context URLs require external documents; embed the context in the input file.')
        for item in value.values():
            _local_json_contexts(item)
    elif isinstance(value, list):
        for item in value:
            _local_json_contexts(item)


def load_rdf_with_metadata(path, rdf_format=None, progress=None):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f'Input must be a local file: {path}')
    if progress:
        progress(1, f'Detecting format: {path.name} ({path.stat().st_size / 1_000_000:.1f} MB)')
    opener = gzip.open if path.suffix.lower() == '.gz' else open
    suffix = Path(path.stem).suffix.lower() if opener is gzip.open else path.suffix.lower()
    with opener(path, 'rb') as stream:
        sample = stream.read(8192).decode('utf-8-sig', errors='replace').lstrip()
    if not sample:
        raise ValueError(f'Input is empty: {path}')
    sniff = None
    if sample.startswith(('{', '[')):
        sniff = 'json-ld'
        with opener(path, 'rt', encoding='utf-8-sig') as stream:
            _local_json_contexts(json.load(stream))
    elif sample.startswith('<?xml') or re.search(r'<(?:\w+:)?(?:RDF|TriX)\b', sample):
        sniff = 'trix' if re.search(r'<(?:\w+:)?TriX\b', sample) else 'xml'
    elif sample.startswith(('@prefix', '@base', 'PREFIX', 'BASE')):
        sniff = 'trig' if suffix in ('.trig', '.nq', '.nquads') else 'turtle'
    if sniff in ('xml', 'trix') or suffix in ('.owl', '.rdf', '.xml'):
        try:
            with opener(path, 'rb') as stream:
                root = next(ET.iterparse(stream, events=('start',)))[1]
                if root.tag == '{http://www.w3.org/2002/07/owl#}Ontology':
                    raise ValueError('OWL/XML is not RDF/XML; serialize the ontology as RDF/XML, Turtle or JSON-LD first.')
        except ET.ParseError:
            pass  # A misleading XML/OWL extension may contain another serialization.
    candidates = [rdf_format] if rdf_format else list(dict.fromkeys(filter(None, [sniff, EXTENSIONS.get(suffix), *FORMATS])))
    errors = {}
    for fmt in candidates:
        # Each attempt gets a fresh graph: a failed parser can leave partial triples.
        if progress:
            progress(2, f'Parsing {fmt}: {path.name} (elapsed timer remains live during parsing)')
        parsed = Dataset(default_union=True) if fmt in ('trig', 'nquads', 'trix', 'json-ld') else Graph()
        try:
            with opener(path, 'rb') as stream:
                parsed.parse(file=stream, publicID=path.as_uri(), format=fmt)
            graph_count = 1
            if isinstance(parsed, Dataset):
                if progress:
                    progress(2, 'Combining named graphs')
                graph_count = sum(bool(len(context)) for context in parsed.graphs())
                graph = Graph()
                for prefix, namespace in parsed.namespaces():
                    graph.bind(prefix, namespace)
                for triple in parsed.triples((None, None, None)):
                    graph.add(triple)
            else:
                graph = parsed
            if not len(graph):
                raise ValueError('No RDF triples were found')
            metadata = {'path': str(path), 'format': fmt, 'compressed': opener is gzip.open,
                                     'nonempty_graphs': graph_count,
                                     'named_graph_policy': 'Union of all graphs; graph provenance is not represented in the Aura schema.'}
            return graph, metadata
        except Exception as exc:
            errors[fmt] = f'{type(exc).__name__}: {str(exc)[:240]}'
    raise ValueError(f'Cannot parse RDF file {path.name}. Supported: {", ".join(FORMATS)}. OWL Functional Syntax and OWL/XML are not RDF/XML. Parser diagnostics: {errors}')


def load_rdf(path, rdf_format=None):
    """Compatibility graph-only API. New pipelines consume explicit metadata."""
    graph, metadata = load_rdf_with_metadata(path, rdf_format)
    graph.source_metadata = metadata
    return graph
