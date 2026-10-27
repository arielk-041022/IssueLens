"""Offline linkage of saved final rankings, pinned index and Validator reports.

No validator execution, retrieval, API, semantic certification or test execution.
Source-byte results are reproduced from the supplied saved report, not renewed.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from evidence_validator import compute_evidence_id, strict_json

COORDS = ('start_line','start_column','end_line','end_column')
MISMATCH = {'STALE_SOURCE','SNIPPET_MISMATCH','OWNERSHIP_ERROR','INVALID_RANGE','EVIDENCE_ID_MISMATCH'}
CHECKS = {'INDEX_BINDING','SAFE_PATH','SOURCE_HASH','PARSE_AND_IDENTITY',
          'METHOD_CONTAINMENT','UTF16_RANGE','EVIDENCE_ID','EXACT_SNIPPET'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def final_rows(document, pool=None):
    """Only explicit identity rows or the actual ACC-006 positional permutation."""
    rows = document if isinstance(document,list) else document.get('ranking')
    require(isinstance(rows,list) and bool(rows), 'NO_SAVED_FINAL_RANKING')
    if all(type(v) is int for v in rows):
        require(isinstance(document,dict) and document.get('status')=='SUCCESS'
                and isinstance(pool,dict) and isinstance(pool.get('candidates'),list), 'POSITIONAL_POOL_REQUIRED')
        candidates = pool['candidates']
        require(sorted(rows)==list(range(1,len(candidates)+1)), 'INVALID_POSITIONAL_PERMUTATION')
        return [deepcopy(candidates[n-1]) for n in rows]
    require(all(isinstance(r,dict) for r in rows), 'IDENTITY_ROWS_REQUIRED')
    return deepcopy(rows)


def trace(document, index, metadata, report, snapshot, hashes, pool=None):
    rows = final_rows(document,pool)
    require(isinstance(index,list) and isinstance(metadata,dict) and isinstance(report,dict)
            and isinstance(snapshot,dict), 'INVALID_TRACE_INPUT')
    method_map = {}
    for f in index:
        for cls in f['classes']:
            for method in cls['methods']:
                method_map.setdefault((f['file'],method.get('method_id')),[]).append((f,method))
    source_manifest = sorted([{'file_path':f['file'],'source_sha256':f['source_sha256']} for f in index],
                             key=lambda f:f['file_path'])
    pins_ok = (metadata.get('index_sha256')==hashes.get('index')==snapshot.get('index_sha256')
               ==report.get('inputs',{}).get('index_sha256')
               and hashes.get('metadata')==snapshot.get('metadata_sha256')
               ==report.get('inputs',{}).get('metadata_sha256'))
    pins_ok = pins_ok and all(isinstance(hashes.get(k),str) and len(hashes[k])==64
                             and all(c in '0123456789abcdef' for c in hashes[k]) for k in ('index','metadata'))
    supported = (metadata.get('schema_version')=='2.0' and metadata.get('method_id_scheme')=='java-source-v1'
                 and report.get('validator_version')=='evidence-local-v2'
                 and report.get('verification_scope',{}).get('protocol')=='evidence-local-v2'
                 and report.get('verification_scope',{}).get('certification')=='PINNED_INDEX_SOURCE_BYTE_CORRESPONDENCE')
    duplicate = [(r.get('repo_id'),r.get('source_snapshot_id'),r.get('file_path'),r.get('method_id')) for r in rows]
    output=[]
    for final_rank,row in enumerate(rows,1):
        mid=row.get('method_id');path=row.get('file_path')
        identity={k:row.get(k) for k in ('repo_id','source_snapshot_id','file_path','method_id')}
        protocol = mid.split(':',1)[0] if isinstance(mid,str) and ':' in mid else 'UNKNOWN'
        item={'final_rank':final_rank,'original_rank':row.get('rank'), 'ordinal':row.get('ordinal'),
              'method_identity':identity,'method_identity_protocol':protocol,
              'trace_status':'UNBOUND','source_fact_status':'UNVERIFIABLE','unverified_reasons':[],
              'evidence':[], 'snapshot':deepcopy(snapshot),
              'verification_scope':deepcopy(report.get('verification_scope',{}))}
        reasons=item['unverified_reasons']
        if protocol!='java-source-v1' or not supported:
            reasons.append('IDENTITY_PROTOCOL_INCOMPATIBLE_WITH_SAVED_VALIDATOR')
        if not all(isinstance(v,str) and v for v in identity.values()):
            reasons.append('INCOMPLETE_CANDIDATE_IDENTITY')
        if report.get('index_integrity',{}).get('status')!='PASS':
            reasons.append('SAVED_VALIDATOR_INDEX_INTEGRITY_NOT_PASS')
        key=tuple(identity[k] for k in ('repo_id','source_snapshot_id','file_path','method_id'))
        if duplicate.count(key)!=1:
            reasons.append('DUPLICATE_FINAL_CANDIDATE_IDENTITY')
        # The saved candidate must itself bind the exact index; a fresh caller label is insufficient.
        if not (pins_ok and row.get('index_sha256')==hashes.get('index')
                and row.get('repo_id')==snapshot.get('repo_id')
                and row.get('source_snapshot_id')==snapshot.get('source_snapshot_id')):
            reasons.append('SOURCE_SNAPSHOT_BINDING_NOT_PROVEN')
        matches=method_map.get((path,mid),[])
        if len(matches)!=1:
            reasons.append('METHOD_IDENTITY_NOT_UNIQUE_IN_PINNED_INDEX')
        elif matches[0][1].get('identity_status')!='AVAILABLE':
            reasons.append('INDEX_METHOD_IDENTITY_UNAVAILABLE')
        if reasons:
            output.append(item);continue
        f,method=matches[0]
        item['trace_status']='BOUND'
        item['snapshot_binding_basis']='SAVED_CANDIDATE_INDEX_SHA256_EQUALS_REPORT_AND_METADATA_PINS'
        item['complete_index_source_manifest_sha256']=sha(json.dumps(source_manifest,ensure_ascii=False,
            sort_keys=True,separators=(',',':')).encode())
        expected=method.get('evidence_occurrences',[])
        if len({e.get('evidence_id') for e in expected})!=len(expected):
            reasons.append('DUPLICATE_INDEX_EVIDENCE_ID')
            output.append(item);continue
        for e in expected:
            eid=e.get('evidence_id');coords={k:e.get(k) for k in COORDS}
            ev={'evidence_id':eid,'evidence_type':e.get('evidence_type'),'source_range':coords,
                'status':'UNVERIFIABLE','unverified_reasons':[], 'validator_result':None}
            found=[v for v in report.get('evidence_results',[]) if v.get('evidence_id')==eid]
            if len(found)!=1:
                ev['unverified_reasons'].append('MISSING_OR_DUPLICATE_VALIDATOR_EVIDENCE_RECORD')
            else:
                saved=found[0]
                ev['validator_result']=deepcopy(saved)
                if (saved.get('method_id')!=mid or saved.get('file_path')!=path
                        or saved.get('evidence_type')!=e.get('evidence_type') or saved.get('range')!=coords):
                    ev['unverified_reasons'].append('EVIDENCE_EXACT_IDENTITY_OR_RANGE_MISMATCH')
                elif saved.get('status')=='VERIFIED':
                    basis=saved.get('basis',{})
                    try:
                        computed=compute_evidence_id(path,f['source_sha256'],mid,e['evidence_type'],[coords[k] for k in COORDS])
                        valid=(computed==eid==basis.get('computed_evidence_id')==basis.get('expected_evidence_id')
                            and basis.get('expected_source_sha256')==basis.get('actual_source_sha256')==f['source_sha256']
                            and basis.get('expected_snippet_sha256')==basis.get('actual_snippet_sha256')
                            ==sha(e['source_snippet'].encode())
                            and basis.get('extracted_source_snippet')==e['source_snippet']
                            and CHECKS<=set(basis.get('checks_passed',[])))
                    except (KeyError,TypeError,ValueError):
                        valid=False
                    if valid:ev['status']='SOURCE_FACT_MATCH_RECORDED'
                    else:ev['unverified_reasons'].append('SAVED_VERIFIED_RECORD_BASIS_INCONSISTENT')
                elif saved.get('status') in MISMATCH:
                    ev['status']='SOURCE_FACT_INCONSISTENCY_RECORDED'
                else:
                    ev['unverified_reasons'].append(saved.get('reason_code') or 'SAVED_RECORD_NOT_VERIFIED')
            item['evidence'].append(ev)
        if not expected:
            reasons.append('NO_INDEXED_EVIDENCE_FOR_CANDIDATE')
        elif any(e['status']=='SOURCE_FACT_INCONSISTENCY_RECORDED' for e in item['evidence']):
            item['source_fact_status']='SOURCE_FACT_INCONSISTENCY_RECORDED'
        elif all(e['status']=='SOURCE_FACT_MATCH_RECORDED' for e in item['evidence']):
            item['source_fact_status']='SOURCE_FACT_MATCH_RECORDED'
        else:
            if any(e['status']=='SOURCE_FACT_MATCH_RECORDED' for e in item['evidence']):
                item['source_fact_status']='PARTIAL_SOURCE_FACT_MATCH_RECORDED'
            reasons.append('MISSING_OR_UNVERIFIED_EVIDENCE_RECORDS')
        output.append(item)
    return {'schema_version':'issuelens-evidence-trace-v0.1','candidates':output,
        'input_sha256':deepcopy(hashes),'validator_original_overall_verdict':report.get('overall_verdict'),
        'validator_original_selected_evidence':deepcopy(report.get('selected_evidence')),
        'runtime_tests':{'status':'NOT_ESTABLISHED_BY_TRACE','reason':'Printed commands do not prove execution'},
        'limits':['Saved-report trace only; no new Validator certification or freshness guarantee',
                  'Caller must trust supplied artifact hash provenance; no independent AST/semantic/root-cause proof',
                  'Snapshot label and optional commit are recorded; shared pinned index proves the byte-source linkage, not Git history',
                  'Model protocol VALID is not evidence VERIFIED; no v1/v2 identity equivalence is inferred']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ('ranking','index','metadata','validator-report','snapshot','output'):
        parser.add_argument('--'+flag,required=True,type=Path)
    parser.add_argument('--candidate-pool',type=Path,help='ACC-006 saved candidates for positional rankings')
    args=parser.parse_args()
    if args.output.exists():parser.exit(2,'OUTPUT_ALREADY_EXISTS\n')
    files={name:getattr(args,name.replace('-','_')) for name in
           ('ranking','index','metadata','validator-report','snapshot')}
    if args.candidate_pool:files['candidate-pool']=args.candidate_pool
    if args.output.resolve() in [p.resolve() for p in files.values()]:parser.exit(2,'OUTPUT_IS_INPUT\n')
    try:
        require(not any('private' in part.lower() for p in files.values() for part in p.resolve().parts),
                'PRIVATE_INPUT_FORBIDDEN')
        raw={name:p.read_bytes() for name,p in files.items()}
        docs={name:strict_json(content) for name,content in raw.items()}
        result=trace(docs['ranking'],docs['index'],docs['metadata'],docs['validator-report'],docs['snapshot'],
                     {name:sha(content) for name,content in raw.items()},docs.get('candidate-pool'))
        text=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n'
        with args.output.open('x',encoding='utf-8') as stream:stream.write(text)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        parser.exit(2,'TRACE_INPUT_ERROR: '+str(exc)+'\n')


if __name__=='__main__':main()
