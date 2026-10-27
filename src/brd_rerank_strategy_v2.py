"""v2 identity adapter, frozen brd-v1 instructions and unchanged HTTP parameters.
No transport. Old BRD and live adapters remain pinned to their v1 wire schemas.
"""
from copy import deepcopy
import hashlib
import math

from .brd_rerank_strategy import BRD_SYSTEM_INSTRUCTIONS, SYSTEM_INSTRUCTIONS, canonical_json, _parse
from .metadata_experiment_v2 import validate_metadata_request, identity_ok, require, digest

PARAMETERS = {'model':'deepseek-flash', 'response_format':{'type':'json_object'}, 'max_tokens':2048,
              'stream':False, 'thinking':{'type':'disabled'}, 'temperature':0}


def build_pair(request, reference, ranking):
    validate_metadata_request(request, reference)
    require(ranking.get('schema_version') == 'metadata-ranking-v2' and ranking.get('algorithm') == 'bm25'
            and ranking.get('status') == 'SUCCESS' and ranking.get('request_sha256') == request['request_sha256'], 'V2_RANK_BINDING')
    rows = ranking['ranking']
    require(len(rows) == request['catalog_count'] >= 20, 'V2_RANK_COVERAGE')
    seen = set()
    for n, row in enumerate(rows, 1):
        ordinal = row.get('ordinal')
        require(type(ordinal) is int and 0 <= ordinal < len(rows) and ordinal not in seen and type(row['rank']) is int and row['rank'] == n, 'V2_RANK_ORDER')
        seen.add(ordinal)
        require({k:v for k,v in row.items() if k not in ('rank','score')} == request['catalog'][ordinal], 'V2_CANDIDATE_CHANGED')
        require(type(row['score']) in (int,float) and math.isfinite(row['score']) and row['score'] >= 0, 'V2_SCORE')
    require(rows == sorted(rows, key=lambda r:(-r['score'],r['ordinal'])), 'V2_RANK_SORT')
    candidates = []
    for row in rows[:20]:
        identity_ok(row)
        source = request['source_contexts'][row['ordinal']]
        c = {k:deepcopy(row[k]) for k in ('repo_id','source_snapshot_id','file_path','method_id','identity_signature',
                                         'ordinal','has_body','features','identity_status','location_status')}
        c.update(bm25_rank=row['rank'],bm25_score=row['score'],source_implementation={
            'source_path':row['file_path'], 'extraction_status':source['source_status'],
            'method_declaration_and_body':source['source_excerpt']})
        candidates.append({'candidate_id':row['rank'],'candidate':c})
    user = canonical_json({'content_role':'UNTRUSTED_DATA_ONLY','issue_text':request['case_metadata']['issue_text'],
                           'candidates':candidates}).decode('utf-8')
    c = {**deepcopy(PARAMETERS), 'messages':[{'role':'system','content':SYSTEM_INSTRUCTIONS},{'role':'user','content':user}]}
    brd = deepcopy(c); brd['messages'][0]['content'] = BRD_SYSTEM_INSTRUCTIONS
    require(len(canonical_json(brd)) <= 200000, 'V2_PAYLOAD_LIMIT')
    pair = {'schema_version':'brd-pair-identity-v2', 'prompt_version':'brd-v1',
            'metadata_request_sha256':request['request_sha256'], 'C':c,'BRD':brd,
            'C_http_sha256':digest(c),'BRD_http_sha256':digest(brd),
            'C_prompt_sha256':hashlib.sha256(SYSTEM_INSTRUCTIONS.encode()).hexdigest(),
            'BRD_prompt_sha256':hashlib.sha256(BRD_SYSTEM_INSTRUCTIONS.encode()).hexdigest()}
    pair['pair_sha256'] = digest(pair)
    return pair


def validate_response(pair, content, *, arm):
    failure = {'status':'INVALID_RESPONSE','ranking':[],'model_reason':None}
    try:
        require(arm in ('C','BRD') and pair['schema_version'] == 'brd-pair-identity-v2', 'V2_PAIR_VERSION')
        require(pair['pair_sha256'] == digest({k:v for k,v in pair.items() if k!='pair_sha256'}), 'V2_PAIR_HASH')
        c, brd = deepcopy(pair['C']), deepcopy(pair['BRD'])
        require(c['messages'][0]['content'] == SYSTEM_INSTRUCTIONS and brd['messages'][0]['content'] == BRD_SYSTEM_INSTRUCTIONS, 'V2_PROMPT')
        brd['messages'][0]['content'] = c['messages'][0]['content']
        require(brd == c and {k:v for k,v in c.items() if k!='messages'} == PARAMETERS, 'V2_PAIR_INVARIANCE')
        require(pair['C_http_sha256'] == digest(pair['C']) and pair['BRD_http_sha256'] == digest(pair['BRD']), 'V2_HTTP_HASH')
        require(pair['C_prompt_sha256'] == hashlib.sha256(SYSTEM_INSTRUCTIONS.encode()).hexdigest()
                and pair['BRD_prompt_sha256'] == hashlib.sha256(BRD_SYSTEM_INSTRUCTIONS.encode()).hexdigest(), 'V2_PROMPT_HASH')
        candidates = _parse(c['messages'][1]['content'])['candidates']
        require(len(candidates)==20 and [r['candidate_id'] for r in candidates]==list(range(1,21)), 'V2_TOP20')
        identities = set()
        for row in candidates:
            identity_ok(row['candidate'])
            require(row['candidate']['method_id'] not in identities, 'V2_DUPLICATE_IDENTITY')
            identities.add(row['candidate']['method_id'])
        require(type(content) is str and len(content)<=16000, 'V2_RESPONSE_LIMIT')
        data = _parse(content)
        require(type(data) is dict and set(data)=={'ranking','reason'}, 'V2_RESPONSE_FIELDS')
        require(type(data['ranking']) is list and all(type(n) is int for n in data['ranking'])
                and sorted(data['ranking'])==list(range(1,21)), 'V2_RESPONSE_PERMUTATION')
        require(type(data['reason']) is str and bool(data['reason'].strip()) and len(data['reason'])<=2000, 'V2_RESPONSE_REASON')
        canonical_json(data)
        return {'status':'SUCCESS','ranking':data['ranking'],'model_reason':{'text':data['reason'],'status':'MODEL_GENERATED_UNVERIFIED'}}
    except (ValueError, KeyError, TypeError):
        return failure
