"""GT-free in-memory metadata sorting, NOT an experimental execution grant.

rank_metadata(request, bindings, *, algorithm='rule' or 'bm25') first calls
DATA08's public validator. It never calls a legacy predict/main/authorization
entry point, loads data files, initializes a tokenizer, or reads the environment.

Rule prerequisite: the caller must explicitly import the unchanged `retriever`
module and initialize its original Jieba tokenizer BEFORE calling this function.
Cold/missing dependencies fail closed; no replacement tokenizer is supplied.
Normal Python imports load dependency code when importing this module; the
ranking function itself performs no file I/O. BM25 needs no Jieba dependency.

No code/source origin is authenticated here: DATA08 bindings remain caller
references. Local installed scorer modules/tokenizer state must be trusted and
held fixed by the caller; this library is not a sandbox for malicious Python.
No real-case CLI, permission issuer, output writer, GT loader or evaluator exists.
"""
from copy import deepcopy
import hashlib
import json
import math
import sys

import bm25_baseline as _bm25
from metadata_experiment import validate_metadata_request


def rank_metadata_v2(request, reference, *, algorithm):
    """Explicit v2 consumer. Same scorer, documents, parameters and tie order.
    reference is an independently pinned output of the v2 source-binding builder.
    v1 entry points deliberately do not dispatch to this function.
    """
    from metadata_experiment_v2 import validate_metadata_request as validate_v2
    validate_v2(request, reference)
    _require(algorithm in ('rule', 'bm25'), 'UNKNOWN_ALGORITHM')
    catalog = deepcopy(request['catalog'])
    scores = _scores(catalog, request['case_metadata']['issue_text'], algorithm) if catalog else []
    _require(len(scores) == len(catalog), 'SCORE_CATALOG_COVERAGE_MISMATCH')
    for score in scores:
        _finite_score(score, nonnegative=algorithm == 'bm25')
    order = sorted(range(len(catalog)), key=lambda i: (-scores[i], catalog[i]['ordinal']))
    return {'schema_version':'metadata-ranking-v2', 'algorithm':algorithm,
            'status':'SUCCESS' if catalog else 'EMPTY', 'request_sha256':request['request_sha256'],
            'catalog_count':len(catalog), 'ranking':[{**catalog[i], 'rank':n, 'score':scores[i]}
                                                   for n,i in enumerate(order,1)],
            'assurance':{**deepcopy(request['assurance']), 'prediction_performed':bool(catalog)}}


class MetadataRankingError(ValueError):
    """Fixed diagnostic code, without issue text or raw dependency exceptions."""

    def __init__(self, reason_code):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require(condition, reason):
    if not condition:
        raise MetadataRankingError(reason)


def _finite_score(value, *, nonnegative):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, ValueError):
        valid = False
    _require(valid, 'NONFINITE_OR_INVALID_SCORE')
    _require(not nonnegative or value >= 0, 'NEGATIVE_BM25_SCORE')


def _rule_module():
    scorer = sys.modules.get('retriever')
    _require(scorer is not None, 'RULE_DEPENDENCY_NOT_PRELOADED')
    _require(callable(getattr(scorer, 'extract_keywords', None))
             and callable(getattr(scorer, 'score_method', None)), 'RULE_SCORER_UNAVAILABLE')
    tokenizer = getattr(getattr(scorer, 'jieba', None), 'dt', None)
    _require(getattr(tokenizer, 'initialized', None) is True, 'RULE_TOKENIZER_NOT_INITIALIZED')
    return scorer


def _scores(catalog, issue_text, algorithm):
    if algorithm == 'rule':
        scorer = _rule_module()
        keywords = scorer.extract_keywords(issue_text)
        scores = []
        for record in catalog:
            # Same seven fields and default disable=None as the original B3 path.
            score, _detail = scorer.score_method(deepcopy(record['features']), keywords, issue_text)
            scores.append(score)
        return scores
    _require(_bm25.K1 == 1.2 and _bm25.B == 0.75, 'FROZEN_BM25_PARAMETERS_CHANGED')
    documents = [_bm25.tokenize(_bm25.document(deepcopy(record['features']))) for record in catalog]
    query = _bm25.tokenize(issue_text)
    scores, _statistics = _bm25.score_tokens(documents, query)
    return scores


def rank_metadata(request, bindings, *, algorithm):
    """Return SUCCESS/EMPTY full metadata order; invalid inputs/scores raise.

    Invalid DATA08 requests raise its MetadataRequestError before scoring.
    Invalid algorithms, missing/cold Rule dependencies, malformed/nonfinite
    scores and scorer exceptions raise MetadataRankingError. There is no partial
    result, fallback, algorithm substitution, retry or automatic authorization.
    KeyboardInterrupt/SystemExit are not swallowed. Empty catalogs are legitimate
    EMPTY results (no scorer invocation), not a way to hide execution failures.

    Every output ranking row is a detached original catalog row plus one-based
    `rank` and the original finite numeric `score`. Original ordinal, identity,
    features, has_body and both availability statuses are retained. Sort key is
    (-score, original ordinal); Rule negative scores are preserved. No timing,
    token, cost or statistical accuracy numbers are invented.
    """
    snapshot = deepcopy(request)
    reference = deepcopy(bindings)
    validation = validate_metadata_request(snapshot, reference)
    _require(type(algorithm) is str and algorithm in ('rule', 'bm25'), 'UNKNOWN_ALGORITHM')
    catalog = snapshot['catalog']
    try:
        scores = _scores(catalog, snapshot['case_metadata']['issue_text'], algorithm) if catalog else []
        _require(type(scores) is list and len(scores) == len(catalog), 'SCORE_CATALOG_COVERAGE_MISMATCH')
        for score in scores:
            _finite_score(score, nonnegative=algorithm == 'bm25')
    except MetadataRankingError:
        raise
    except Exception:
        raise MetadataRankingError('SCORER_EXECUTION_ERROR') from None
    order = sorted(range(len(catalog)), key=lambda i: (-scores[i], catalog[i]['ordinal']))
    ranking = [{**deepcopy(catalog[i]), 'rank': rank, 'score': scores[i]}
               for rank, i in enumerate(order, 1)]
    # The DATA08 request still truthfully says prediction_performed=False at
    # preparation time. Only this separate result reports the sorting operation.
    assurance = deepcopy(validation['assurance'])
    assurance['prediction_performed'] = bool(catalog)
    result = {
        'schema_version': 'metadata-ranking-v1',
        'status': 'SUCCESS' if catalog else 'EMPTY',
        'operation': 'METADATA_RANKING_ONLY',
        'algorithm': algorithm,
        'case_id': snapshot['case_metadata']['case_id'],
        'repo_id': snapshot['case_metadata']['repo_id'],
        'source_snapshot_id': snapshot['case_metadata']['source_snapshot_id'],
        'input_version_status': snapshot['case_metadata']['input_version_status'],
        'request_sha256': validation['request_sha256'],
        'catalog_sha256': reference['catalog_sha256'],
        'catalog_count': len(catalog),
        'scored_count': len(scores),
        'ranking': ranking,
        'validator_summary': deepcopy(reference['validator_summary']),
        'validator_report_sha256': reference['validator_report_sha256'],
        'location_status_counts': deepcopy(validation['location_status_counts']),
        'assurance': assurance,
        'scorer_binding_status': 'LOCAL_DEPENDENCY_NOT_ATTESTED',
        'reference_code_sha256': deepcopy(reference['code_sha256']),
        'fallback_applied': False,
    }
    raw = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                     allow_nan=False).encode('utf-8')
    result['result_sha256'] = hashlib.sha256(raw).hexdigest()
    return result
