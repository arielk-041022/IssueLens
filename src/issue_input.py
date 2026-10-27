"""Lossless CLI Issue ingestion; no retrieval, network or GT dependencies."""
import hashlib
import json
from pathlib import Path

DEFAULT_MAX_BYTES = 200_000
PROJECT = Path(__file__).resolve().parents[1]


class IssueInputError(ValueError):
    pass


def add_arguments(parser):
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--issue-title', help='原始 Issue 标题；可配合正文')
    source.add_argument('--issue-file', help='完整原始 Issue 文本的 UTF-8 文件，不解析或删改内容')
    body = parser.add_mutually_exclusive_group()
    body.add_argument('--issue-body', help='原始正文，与标题以两个 LF 连接')
    body.add_argument('--issue-body-file', help='原始正文 UTF-8 文件；须配合 --issue-title')
    parser.add_argument('--repo', help='直接输入模式的修复前仓库路径')
    parser.add_argument('--issue-url', help='用户提供的来源 URL，仅记录，不联网')
    parser.add_argument('--issue-record', help='原文与来源/哈希 JSON，拒绝覆盖；不得写入 experiments')
    parser.add_argument('--max-issue-bytes', type=int, default=DEFAULT_MAX_BYTES,
                        help='最大完整输入 UTF-8 字节数（含日志/代码块，默认200000）；超限报错，不截断')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def bounded_file(path, limit):
    p = Path(path).resolve()
    try:
        with p.open('rb') as f:
            raw = f.read(limit + 1)
    except OSError as exc:
        raise IssueInputError(f'无法读取 Issue 文件 {p}: {exc}') from exc
    if len(raw) > limit:
        raise IssueInputError(f'ISSUE_INPUT_TOO_LARGE: 文件超过 {limit} 字节；未截断，请显式调整 --max-issue-bytes')
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise IssueInputError(f'INVALID_UTF8: {p}；请提供有效 UTF-8，未替换非法字节') from exc
    return text, {'kind': 'utf8-file', 'path': str(p), 'sha256': digest(raw), 'bytes': len(raw)}


def validate_text(text, limit):
    if not isinstance(text, str) or not text.strip():
        raise IssueInputError('Issue 文本不能为空')
    try:
        raw = text.encode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise IssueInputError('INVALID_UTF8: 文本包含无效 Unicode 字符') from exc
    if len(raw) > limit:
        raise IssueInputError(f'ISSUE_INPUT_TOO_LARGE: {len(raw)} > {limit} UTF-8 字节；未截断，请显式调整 --max-issue-bytes')
    return raw


def direct_case(args, parser):
    """Return a new case, or None to preserve the existing benchmark selection."""
    if args.max_issue_bytes <= 0:
        parser.error('--max-issue-bytes 必须为正整数')
    direct = args.issue_title is not None or args.issue_file is not None
    if not direct:
        if any(v is not None for v in (args.issue_body, args.issue_body_file, args.repo)):
            parser.error('--issue-body/--issue-body-file/--repo 需要 --issue-title 或 --issue-file')
        return None
    if args.id is not None:
        parser.error('直接 Issue 输入不能同时使用 benchmark --id')
    if not args.repo:
        parser.error('直接 Issue 输入需要 --repo')
    if args.issue_title is None and (args.issue_body is not None or args.issue_body_file is not None):
        parser.error('正文参数需要 --issue-title，不能与 --issue-file 混用')
    try:
        sources = []
        title, body = args.issue_title, args.issue_body
        if args.issue_file is not None:
            text, src = bounded_file(args.issue_file, args.max_issue_bytes)
            sources.append(src)
            kind = 'full-text-file'
        else:
            validate_text(title, args.max_issue_bytes)
            sources.append({'kind': 'argument', 'field': 'title', 'sha256': digest(title.encode('utf-8'))})
            if args.issue_body_file is not None:
                body, src = bounded_file(args.issue_body_file, args.max_issue_bytes)
                sources.append({**src, 'field': 'body'})
            elif body is not None:
                sources.append({'kind': 'argument', 'field': 'body', 'sha256': digest(body.encode('utf-8'))})
            text = title + ('\n\n' + body if body else '')
            kind = 'title-body' if body else 'title-only'
        raw = validate_text(text, args.max_issue_bytes)
    except (IssueInputError, UnicodeError) as exc:
        parser.error(str(exc))
    return {'id': 'cli-issue', 'repo_path': args.repo, 'issue_text': text,
            '_issue_input': {'schema': 'issuelens-issue-input-v1', 'kind': kind,
                             'title': title, 'body': body, 'sources': sources,
                             'issue_text_sha256': digest(raw), 'utf8_bytes': len(raw),
                             'max_issue_bytes': args.max_issue_bytes}}


def record_input(case, args, parser):
    """Metadata only: never edit benchmark cases on disk or normalize Issue text."""
    case = dict(case)
    try:
        raw = validate_text(case.get('issue_text'), args.max_issue_bytes)
        metadata = case.get('_issue_input')
        if metadata is None:
            p = Path(args.benchmark).resolve()
            metadata = {'schema': 'issuelens-issue-input-v1', 'kind': 'benchmark',
                        'sources': [{'kind': 'benchmark', 'path': str(p), 'case_id': case.get('id'),
                                     'sha256': digest(p.read_bytes())}],
                        'issue_text_sha256': digest(raw), 'utf8_bytes': len(raw),
                        'max_issue_bytes': args.max_issue_bytes}
        metadata = {**metadata, 'url': args.issue_url, 'url_verification': 'USER_PROVIDED_NOT_FETCHED' if args.issue_url else None}
        case['_issue_input'] = metadata
        if args.issue_record:
            p = Path(args.issue_record).resolve()
            if p.is_relative_to((PROJECT / 'experiments').resolve()):
                raise IssueInputError('INPUT_RECORD_PROTECTED_PATH: 不得写入冻结实验目录')
            record = {'issue_text': case['issue_text'], 'provenance': metadata}
            encoded = (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open('xb') as f:
                f.write(encoded)
    except (IssueInputError, OSError, UnicodeError) as exc:
        parser.error(str(exc))
    print(' Issue input: ' + json.dumps({k: v for k, v in metadata.items() if k not in ('title', 'body')}, ensure_ascii=False))
    return case
