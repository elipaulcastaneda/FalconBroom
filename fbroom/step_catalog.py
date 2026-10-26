"""Single source of truth for recipe steps shared by suggestions, templates and the text parser.

Every step shape is expressible three ways that must stay equivalent:
  * a structured step dict (what the engine runs),
  * a compact template line (`cast column=x to=float`),
  * a plain-English clause (`cast x to float`).
`step_to_template`/`step_to_text` serialize a step; `parse_machine_template`/`parse_instruction`
read it back. tests/test_parser_suggestion_parity.py asserts the round trips.
"""
from __future__ import annotations

import re
from typing import Any

TEMPLATE_ACTIONS = (
    'normalize', 'impute', 'cast', 'coerce', 'regex_replace', 'map', 'numeric_transform',
    'deduplicate', 'drop_column', 'rename', 'unicode_normalize', 'replace', 'move_by_type',
)

_TYPE_WORDS = {
    'timestamp': 'datetime', 'datetime': 'datetime', 'date': 'datetime',
    'boolean': 'bool', 'bool': 'bool',
    'integer': 'int', 'int': 'int',
    'decimal': 'float', 'numeric': 'float', 'number': 'float', 'float': 'float',
    'string': 'str', 'text': 'str', 'str': 'str',
}
_TYPE_TEXT = {'int': 'integer', 'float': 'float', 'str': 'string', 'datetime': 'datetime', 'bool': 'boolean'}
_CASE_TEXT = {
    'upper': 'uppercase {c}',
    'lower': 'lowercase {c}',
    'trim': 'trim whitespace in {c}',
    'title': 'title case {c}',
    'capitalize': 'capitalize {c}',
}
_UNSAFE_MAP = re.compile(r"[,;:=]|->")


def _num(value: Any) -> Any:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number


def _quote(value: Any) -> str | None:
    text = str(value)
    return None if '"' in text else '"' + text + '"'


def _token(value: Any) -> str | None:
    text = str(value)
    if text and re.fullmatch(r"[^\s\"'=]+", text):
        return text
    return _quote(text)


def parse_machine_template(mt: str) -> dict | None:
    """Parse one template line such as `cast column=x to=float` into a step dict."""
    try:
        if not mt or not isinstance(mt, str):
            return None
        s = mt.strip()
        toks = s.split()
        action = toks[0].lower()
        rest = s[len(toks[0]):].strip()
        kv: dict[str, str] = {}
        for k, v in re.findall(r"(\w+)=('.*?'|\".*?\"|[^\s]+)", rest):
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            kv[k.lower()] = v

        col = kv.get('column')
        if action == 'normalize' and col:
            return {'action': 'normalize', 'column': col, 'params': {'case': kv.get('case') or 'lower'}}
        if action == 'impute' and col:
            params: dict[str, Any] = {'strategy': kv.get('strategy') or kv.get('method') or 'mean'}
            if 'value' in kv:
                params['value'] = _num(kv['value'])
            if kv.get('source'):
                params['source'] = kv['source']
            return {'action': 'impute', 'column': col, 'params': params}
        if action in ('cast', 'coerce') and col and (kv.get('to') or kv.get('type')):
            params = {'to': kv.get('to') or kv.get('type')}
            if kv.get('fmt') or kv.get('format'):
                params['fmt'] = kv.get('fmt') or kv.get('format')
            return {'action': 'cast', 'column': col, 'params': params}
        if action == 'regex_replace' and col and kv.get('pattern') is not None:
            return {'action': 'regex_replace', 'column': col, 'params': {'pattern': kv['pattern'], 'replace': kv.get('replace', '')}}
        if action == 'map' and col and (kv.get('mapping') or kv.get('map')):
            mapping = {}
            for item in re.split(r'[,;]', kv.get('mapping') or kv.get('map')):
                parts = re.split(r'->|:|=', item.strip(), maxsplit=1)
                if len(parts) == 2:
                    mapping[parts[0].strip()] = parts[1].strip()
            if mapping:
                return {'action': 'map', 'column': col, 'params': {'mapping': mapping}}
        if action == 'numeric_transform' and col and kv.get('operation'):
            op = kv['operation'].lower()
            params = {'operation': op}
            if op in ('multiply', 'divide', 'scale'):
                factor = kv.get('factor') or kv.get('value')
                if factor is None:
                    return None
                params['factor'] = _num(factor)
            elif op == 'round':
                params['ndigits'] = int(float(kv.get('ndigits') or kv.get('digits') or 0))
            return {'action': 'numeric_transform', 'column': col, 'params': params}
        if action == 'deduplicate':
            raw = kv.get('column') or kv.get('columns') or ''
            cols = [c.strip() for c in raw.split(',') if c.strip()]
            column: Any = None if not cols else (cols[0] if len(cols) == 1 else cols)
            return {'action': 'deduplicate', 'column': column, 'params': {}}
        if action == 'drop_column' and col:
            return {'action': 'drop_column', 'column': col, 'params': {}}
        if action == 'rename' and col and (kv.get('new_name') or kv.get('to')):
            return {'action': 'rename', 'column': col, 'params': {'new_name': kv.get('new_name') or kv.get('to')}}
        if action == 'unicode_normalize' and col:
            params = {'form': kv.get('form') or 'NFKC'}
            if str(kv.get('remove_diacritics', '')).lower() in ('1', 'true', 'yes'):
                params['remove_diacritics'] = True
            return {'action': 'unicode_normalize', 'column': col, 'params': params}
        if action == 'replace' and col and 'old' in kv:
            return {'action': 'replace', 'column': col, 'params': {'old': kv['old'], 'new': kv.get('new', '')}}
        if action == 'move_by_type' and kv.get('source') and kv.get('target'):
            return {'action': 'move_by_type', 'column': None, 'params': {
                'source': kv['source'], 'target': kv['target'], 'type': kv.get('type') or 'string',
                'exceptions': [], 'replacement': '',
            }}
        return None
    except Exception:
        return None


def step_to_template(step: dict) -> str | None:
    """Serialize a step to a template line, or None when it cannot be written losslessly."""
    action = step.get('action')
    column = step.get('column')
    params = step.get('params') or {}
    col = _token(column) if isinstance(column, str) and column else None
    if action == 'deduplicate':
        if column is None:
            return 'deduplicate'
        cols = [column] if isinstance(column, str) else list(column or [])
        quoted = _quote(','.join(cols))
        return None if quoted is None else 'deduplicate column=' + quoted
    if action == 'move_by_type':
        src, tgt = _token(params.get('source', '')), _token(params.get('target', ''))
        if not src or not tgt or params.get('exceptions') or params.get('replacement'):
            return None
        return f"move_by_type source={src} target={tgt} type={params.get('type', 'string')}"
    if not col:
        return None
    if action == 'normalize':
        return f"normalize column={col} case={params.get('case', 'lower')}"
    if action == 'cast':
        line = f"cast column={col} to={params.get('to')}"
        if params.get('fmt'):
            fmt = _quote(params['fmt'])
            if fmt is None:
                return None
            line += f" fmt={fmt}"
        return line
    if action == 'regex_replace':
        pattern, repl = _quote(params.get('pattern', '')), _quote(params.get('replace', ''))
        if pattern is None or repl is None:
            return None
        return f"regex_replace column={col} pattern={pattern} replace={repl}"
    if action == 'impute':
        line = f"impute column={col} strategy={params.get('strategy', 'mean')}"
        if 'value' in params:
            value = _token(params['value'])
            if value is None:
                return None
            line += f" value={value}"
        if params.get('source'):
            source = _token(params['source'])
            if source is None:
                return None
            line += f" source={source}"
        return line
    if action == 'map':
        mapping = params.get('mapping') or {}
        if not mapping or any(_UNSAFE_MAP.search(str(k) + str(v)) for k, v in mapping.items()):
            return None
        body = _quote(','.join(f"{k}->{v}" for k, v in mapping.items()))
        return None if body is None else f"map column={col} mapping={body}"
    if action == 'numeric_transform':
        op = params.get('operation')
        if op in ('multiply', 'divide', 'scale'):
            return f"numeric_transform column={col} operation={op} factor={params.get('factor')}"
        if op == 'round':
            return f"numeric_transform column={col} operation=round ndigits={params.get('ndigits', 0)}"
        if op == 'abs':
            return f"numeric_transform column={col} operation=abs"
        return None
    if action == 'drop_column':
        return f"drop_column column={col}"
    if action == 'rename':
        new = _token(params.get('new_name', ''))
        return None if not new else f"rename column={col} new_name={new}"
    if action == 'unicode_normalize':
        line = f"unicode_normalize column={col} form={params.get('form', 'NFKC')}"
        return line + (' remove_diacritics=true' if params.get('remove_diacritics') else '')
    if action == 'replace':
        old, new = _quote(params.get('old', '')), _quote(params.get('new', ''))
        return None if old is None or new is None else f"replace column={col} old={old} new={new}"
    return None


def step_to_text(step: dict) -> str | None:
    """Serialize a step to a plain-English clause that `parse_instruction` reads back."""
    action = step.get('action')
    column = step.get('column')
    params = step.get('params') or {}
    c = column if isinstance(column, str) else None
    if action == 'deduplicate':
        if column is None:
            return 'remove duplicate rows'
        cols = [column] if isinstance(column, str) else list(column or [])
        return 'remove duplicate rows based on ' + ', '.join(cols)
    if action == 'move_by_type':
        kind = 'numeric' if params.get('type') in ('numeric', 'numerical') else 'text'
        if params.get('exceptions') or params.get('replacement'):
            return None
        return f"move all {kind} values from {params.get('source')} to {params.get('target')}"
    if not c:
        return None
    if action == 'normalize':
        template = _CASE_TEXT.get(params.get('case', 'lower'))
        return template.format(c=c) if template else None
    if action == 'cast':
        word = _TYPE_TEXT.get(params.get('to'))
        if not word:
            return None
        text = f"cast {c} to {word}"
        if params.get('fmt'):
            fmt = _quote(params['fmt'])
            if fmt is None:
                return None
            text += f" using format {fmt}"
        return text
    if action == 'regex_replace':
        pattern, repl = _quote(params.get('pattern', '')), _quote(params.get('replace', ''))
        if pattern is None or repl is None:
            return None
        return f"replace regex {pattern} with {repl} in {c}"
    if action == 'impute':
        strategy = params.get('strategy')
        if strategy in ('mean', 'median', 'mode') and set(params) <= {'strategy'}:
            return f"fill missing {c} with {strategy}"
        if strategy == 'constant' and 'value' in params:
            value = params['value']
            literal = _quote(value) if isinstance(value, str) else str(value)
            return None if literal is None else f"fill missing {c} with constant {literal}"
        return None
    if action == 'map':
        mapping = params.get('mapping') or {}
        pairs = []
        for key, value in mapping.items():
            qk, qv = _quote(key), _quote(value)
            if qk is None or qv is None:
                return None
            pairs.append(f"{qk} to {qv}")
        return f"map {', '.join(pairs)} in {c}" if pairs else None
    if action == 'numeric_transform':
        op = params.get('operation')
        if op in ('multiply', 'divide'):
            return f"{op} {c} by {params.get('factor')}"
        if op == 'round':
            return f"round {c} to {params.get('ndigits', 0)} decimal places"
        if op == 'abs':
            return f"make negative values in {c} positive"
        return None
    if action == 'drop_column':
        return f"drop column {c}"
    if action == 'rename':
        new = _quote(params.get('new_name', ''))
        return None if new is None else f"rename {c} to {new}"
    if action == 'unicode_normalize':
        text = f"normalize unicode in {c} using {params.get('form', 'NFKC')}"
        return text + (' and remove diacritics' if params.get('remove_diacritics') else '')
    if action == 'replace':
        old, new = _quote(params.get('old', '')), _quote(params.get('new', ''))
        return None if old is None or new is None else f"replace {old} with {new} in {c}"
    return None


def steps_to_template(steps: list[dict]) -> str | None:
    lines = [step_to_template(step) for step in steps]
    return None if not lines or any(line is None for line in lines) else '\n'.join(lines)


def steps_to_text(steps: list[dict]) -> str | None:
    clauses = [step_to_text(step) for step in steps]
    return None if not clauses or any(clause is None for clause in clauses) else '; '.join(clauses)


_QUOTED = r"""(?:"[^"]*"|'[^']*')"""
_NUM = r"-?\d+(?:\.\d+)?"


def _unquote(value: str) -> str:
    value = value.strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value


def _split_names(raw: str) -> list[str]:
    parts = re.split(r"\s*(?:,|\band\b)\s*", raw.strip(), flags=re.I)
    return [_unquote(re.sub(r"^(?:the\s+)?(?:column\s+)?", "", p.strip(), flags=re.I)) for p in parts if p.strip()]


def _clause_steps(clause: str, cols: list[str], last_col: str | None) -> list[dict] | None:
    template_head = '|'.join(TEMPLATE_ACTIONS)
    if re.match(rf"^\s*(?:{template_head})\s+\w+=", clause, re.I):
        step = parse_machine_template(clause)
        return [step] if step else None

    real = {c.lower(): c for c in cols}
    alt = '|'.join(re.escape(c) for c in sorted(cols, key=len, reverse=True)) or r"[A-Za-z_][\w.]*"
    col = r"['\"]?(?P<c>" + alt + r")['\"]?(?:\s+column)?"
    colthe = r"(?:the\s+)?" + col

    if last_col:
        clause = re.sub(r"\b(?:it|them|that column|this column)\b", lambda _m: last_col, clause, flags=re.I)

    def resolve(group: str) -> str | None:
        return real.get(group.lower(), group if group in cols else None)

    def one(column: str | None, action: str, params: dict) -> list[dict] | None:
        return [{'action': action, 'column': column, 'params': params}] if column else None

    flags = re.I
    m = re.fullmatch(r"(?:(?:remove|drop|delete|eliminate)\s+(?:all\s+)?(?:exact\s+)?duplicate(?:s|\s+rows?)?(?:\s+rows?)?|dedupe|deduplicate(?:\s+rows)?)"
                     r"(?:\s+(?:based\s+on|by|using|on)\s+(?P<cols>.+))?", clause, flags)
    if m:
        names = _split_names(m.group('cols')) if m.group('cols') else []
        resolved = [real.get(n.lower()) for n in names]
        if any(r is None for r in resolved):
            return None
        column: Any = None if not resolved else (resolved[0] if len(resolved) == 1 else resolved)
        return [{'action': 'deduplicate', 'column': column, 'params': {}}]

    m = re.fullmatch(rf"(?P<op>divide|multiply)\s+{colthe}\s+by\s+(?P<n>{_NUM})", clause, flags)
    if m and resolve(m.group('c')):
        factor = _num(m.group('n'))
        if m.group('op').lower() == 'divide' and factor == 0:
            return None
        return one(resolve(m.group('c')), 'numeric_transform', {'operation': m.group('op').lower(), 'factor': factor})
    m = re.fullmatch(rf"convert\s+{colthe}\s+from\s+percent(?:age)?(?:\s+points?)?\s+to\s+(?:a\s+)?(?:decimal|fraction)s?", clause, flags)
    if m and resolve(m.group('c')):
        return one(resolve(m.group('c')), 'numeric_transform', {'operation': 'divide', 'factor': 100})
    m = re.fullmatch(rf"round\s+{colthe}\s+to\s+(?P<n>\d+)\s+decimal\s+places?", clause, flags)
    if m and resolve(m.group('c')):
        return one(resolve(m.group('c')), 'numeric_transform', {'operation': 'round', 'ndigits': int(m.group('n'))})
    for abs_pattern in (
        rf"make\s+(?:all\s+)?negative\s+(?:number\s+)?values\s+(?:in|of)\s+{colthe}\s+positive",
        rf"take\s+the\s+absolute\s+value\s+of\s+{colthe}",
    ):
        m = re.fullmatch(abs_pattern, clause, flags)
        if m and resolve(m.group('c')):
            return one(resolve(m.group('c')), 'numeric_transform', {'operation': 'abs'})

    types = '|'.join(sorted(_TYPE_WORDS, key=len, reverse=True))
    m = re.fullmatch(rf"(?:cast|convert|change|coerce|parse)\s+{colthe}\s+(?:values\s+)?(?:to|as|into)\s+(?:an?\s+)?(?P<t>{types})s?"
                     r"""(?:\s+(?:using|with)\s+(?:the\s+)?(?:date\s+)?format\s+(?:"(?P<fq>[^"]+)"|'(?P<fs>[^']+)'|(?P<fu>\S+)))?""", clause, flags)
    if m and resolve(m.group('c')):
        params = {'to': _TYPE_WORDS[m.group('t').lower()]}
        fmt = m.group('fq') or m.group('fs') or m.group('fu')
        if fmt:
            params['fmt'] = fmt
        return one(resolve(m.group('c')), 'cast', params)

    m = re.fullmatch(rf"(?:replace|apply)\s+regex(?:\s+pattern)?\s+(?P<p>{_QUOTED})\s+with\s+(?P<r>{_QUOTED})\s+(?:in|on)\s+{colthe}", clause, flags)
    if m and resolve(m.group('c')):
        return one(resolve(m.group('c')), 'regex_replace', {'pattern': _unquote(m.group('p')), 'replace': _unquote(m.group('r'))})

    m = re.fullmatch(rf"strip\s+(?:the\s+)?(?:(?P<k1>suffix|prefix)\s+)?(?P<t>{_QUOTED}|[^\s'\"]+)(?:\s+(?P<k2>suffix|prefix))?\s+from\s+{colthe}", clause, flags)
    if m and resolve(m.group('c')):
        token = re.escape(_unquote(m.group('t')))
        kind = (m.group('k1') or m.group('k2') or 'suffix').lower()
        pattern = '^' + token + r'\s*' if kind == 'prefix' else r'\s*' + token + '$'
        return one(resolve(m.group('c')), 'regex_replace', {'pattern': pattern, 'replace': ''})

    for marker_pattern in (
        rf"(?:treat|mark|set|convert)\s+(?:the\s+)?(?:values?\s+)?(?P<list>.+?)\s+(?:in|for|from)\s+{colthe}\s+(?:as|to)\s+(?:blank|empty|missing|null)",
        rf"(?:treat|mark|set|convert)\s+(?:the\s+)?(?:values?\s+)?(?P<list>.+?)\s+(?:as|to)\s+(?:blank|empty|missing|null)\s+(?:in|for|from)\s+{colthe}",
    ):
        m = re.fullmatch(marker_pattern, clause, flags)
        if m and resolve(m.group('c')):
            tokens = [_unquote(t) for t in re.split(r"\s*(?:,|\band\b|\bor\b)\s*", m.group('list').strip(), flags=re.I) if t.strip()]
            if tokens:
                pattern = r'(?i)^(?:' + '|'.join(re.escape(t) for t in tokens) + r')$'
                return one(resolve(m.group('c')), 'regex_replace', {'pattern': pattern, 'replace': ''})

    m = re.fullmatch(rf"replace\s+(?P<o>{_QUOTED})\s+with\s+(?P<n>{_QUOTED}|blank|empty|nothing)\s+(?:in|on)\s+{colthe}", clause, flags)
    if m and resolve(m.group('c')):
        new = '' if m.group('n').lower() in ('blank', 'empty', 'nothing') else _unquote(m.group('n'))
        return one(resolve(m.group('c')), 'replace', {'old': _unquote(m.group('o')), 'new': new})

    m = re.fullmatch(rf"map\s+(?P<pairs>.+?)\s+(?:in|on|for)\s+{colthe}", clause, flags)
    if m and resolve(m.group('c')):
        pair = re.compile(r"""(["'])(.*?)\1\s*(?:->|=>|\bto\b)\s*(["'])(.*?)\3""")
        found = pair.findall(m.group('pairs'))
        leftover = pair.sub('', m.group('pairs'))
        if found and not re.sub(r"[\s,;]|\band\b", '', leftover, flags=re.I):
            return one(resolve(m.group('c')), 'map', {'mapping': {k: v for _q1, k, _q2, v in found}})

    m = re.fullmatch(rf"rename\s+(?:the\s+)?(?:column\s+)?{col}\s+to\s+(?:\"(?P<nq>[^\"]+)\"|'(?P<ns>[^']+)'|(?P<nu>\S+))", clause, flags)
    if m and resolve(m.group('c')):
        new = m.group('nq') or m.group('ns') or m.group('nu')
        return one(resolve(m.group('c')), 'rename', {'new_name': new})

    m = re.fullmatch(rf"(?:drop|delete|remove)\s+(?:the\s+)?(?:column\s+)?{col}", clause, flags)
    if m and resolve(m.group('c')):
        return one(resolve(m.group('c')), 'drop_column', {})

    m = re.fullmatch(rf"(?:unicode[- ]normalize|normalize\s+unicode(?:\s+text)?)\s+(?:in|for|of)\s+{colthe}(?:\s+(?:using|with|to)\s+(?P<form>NFC|NFD|NFKC|NFKD))?(?P<dia>\s+and\s+remove\s+diacritics)?", clause, flags)
    if m and resolve(m.group('c')):
        params = {'form': (m.group('form') or 'NFKC').upper()}
        if m.group('dia'):
            params['remove_diacritics'] = True
        return one(resolve(m.group('c')), 'unicode_normalize', params)

    for pattern, case in (
        (rf"(?:uppercase|upper[- ]?case)\s+(?:the\s+)?(?:values\s+(?:in|of)\s+)?{colthe}", 'upper'),
        (rf"(?:lowercase|lower[- ]?case)\s+(?:the\s+)?(?:values\s+(?:in|of)\s+)?{colthe}", 'lower'),
        (rf"(?:trim|strip)\s+(?:the\s+)?whitespace\s+(?:in|from|of)\s+{colthe}", 'trim'),
        (rf"title[- ]?case\s+(?:the\s+)?{colthe}", 'title'),
        (rf"capitalize\s+(?:the\s+)?{colthe}", 'capitalize'),
        (rf"(?:convert|standardize|change)\s+{colthe}\s+to\s+(?:upper[- ]?case|capital\s+letters)", 'upper'),
        (rf"(?:convert|standardize|change)\s+{colthe}\s+to\s+lower[- ]?case", 'lower'),
    ):
        m = re.fullmatch(pattern, clause, flags)
        if m and resolve(m.group('c')):
            return one(resolve(m.group('c')), 'normalize', {'case': case})

    m = re.fullmatch(rf"(?:fill|impute)\s+(?:the\s+)?(?:missing\s+)?(?:values\s+)?(?:in\s+|of\s+)?{colthe}\s+with\s+(?:the\s+)?"
                     rf"(?:(?P<s>mean|average|median|mode|most\s+common(?:\s+value)?)|constant\s+(?P<v>{_QUOTED}|{_NUM}))", clause, flags)
    if m and resolve(m.group('c')):
        if m.group('s'):
            word = m.group('s').lower()
            strategy = 'mean' if word in ('mean', 'average') else ('median' if word == 'median' else 'mode')
            return one(resolve(m.group('c')), 'impute', {'strategy': strategy})
        raw = m.group('v')
        value: Any = _unquote(raw) if raw[0] in "\"'" else _num(raw)
        return one(resolve(m.group('c')), 'impute', {'strategy': 'constant', 'value': value})

    m = re.fullmatch(rf"pad\s+(?P<k>\d+)-digit\s+(?:values\s+)?(?:in|of)\s+{colthe}\s+with\s+(?:a\s+)?leading\s+zero", clause, flags)
    if m and resolve(m.group('c')):
        return one(resolve(m.group('c')), 'regex_replace', {'pattern': r'^(\d{' + m.group('k') + r'})$', 'replace': r'0\1'})

    alt_a = r"['\"]?(?P<a>" + alt + r")['\"]?"
    alt_b = r"['\"]?(?P<b>" + alt + r")['\"]?"
    m = re.fullmatch(rf"move\s+(?:all\s+)?(?P<t>numeric|numerical|text|string)\s+values\s+from\s+(?:the\s+)?{alt_a}(?:\s+column)?\s+(?:to|into)\s+(?:the\s+)?{alt_b}(?:\s+column)?", clause, flags)
    if m and resolve(m.group('a')) and resolve(m.group('b')):
        kind = 'numeric' if m.group('t').lower() in ('numeric', 'numerical') else 'string'
        return [{'action': 'move_by_type', 'column': None, 'params': {
            'source': resolve(m.group('a')), 'target': resolve(m.group('b')), 'type': kind,
            'exceptions': [], 'replacement': '',
        }}]
    return None


def parse_instruction(text: str, columns) -> list[dict] | None:
    """Parse an instruction made only of catalog clauses; None if any clause is not understood."""
    if not text or not str(text).strip():
        return None
    cols = [c for c in columns if isinstance(c, str) and c]
    clauses = [c.strip().rstrip('.').strip() for c in re.split(r"(?i)\s*(?:;|\n|,?\s+and\s+then\s+|,?\s+then\s+)\s*", str(text)) if c and c.strip()]
    steps: list[dict] = []
    last_col: str | None = None
    for clause in clauses:
        parsed = _clause_steps(clause, cols, last_col)
        if not parsed:
            return None
        steps.extend(parsed)
        last = parsed[-1].get('column')
        last_col = last if isinstance(last, str) else last_col
    return steps or None
