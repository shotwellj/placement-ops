"""
One-shot backfill: re-run the upgraded resolver against every existing
unresolved row in req_skills and candidate_skills. Prints before/after
resolution rates and counts of which tier resolved each row.

Safe to re-run. Only updates rows where skill_id IS NULL and the
resolver finds a match. Never overwrites existing skill_id values.
"""
import os, sys, asyncio, httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TURSO_URL = os.environ['TURSO_URL']
TURSO_TOKEN = os.environ['TURSO_AUTH_TOKEN']
if TURSO_URL.startswith('libsql://'):
    TURSO_URL = 'https://' + TURSO_URL[len('libsql://'):]
TURSO_URL = TURSO_URL.rstrip('/')


class _Result:
    def __init__(self, raw):
        self.rows = []
        for raw_row in (raw.get('rows') or []):
            row = []
            for cell in raw_row:
                t = cell.get('type'); v = cell.get('value')
                if t == 'null': row.append(None)
                elif t == 'integer': row.append(int(v) if v is not None else None)
                elif t == 'float': row.append(float(v) if v is not None else None)
                else: row.append(v)
            self.rows.append(row)


class Client:
    def __init__(self):
        self._http = httpx.AsyncClient(timeout=30.0)
    async def execute(self, sql, args=None):
        stmt = {'sql': sql}
        if args:
            stmt['args'] = []
            for a in args:
                if a is None: stmt['args'].append({'type': 'null'})
                elif isinstance(a, bool): stmt['args'].append({'type': 'integer', 'value': '1' if a else '0'})
                elif isinstance(a, int): stmt['args'].append({'type': 'integer', 'value': str(a)})
                elif isinstance(a, float): stmt['args'].append({'type': 'float', 'value': a})
                else: stmt['args'].append({'type': 'text', 'value': str(a)})
        r = await self._http.post(f'{TURSO_URL}/v2/pipeline',
            headers={'Authorization': f'Bearer {TURSO_TOKEN}'},
            json={'requests': [{'type': 'execute', 'stmt': stmt}, {'type': 'close'}]})
        body = r.json()
        first = body.get('results', [{}])[0]
        if first.get('type') == 'error':
            raise RuntimeError(f"SQL error: {first.get('error',{}).get('message')}\n{sql}")
        return _Result(first.get('response', {}).get('result', {}))
    async def close(self): await self._http.aclose()


from api._compliance import resolve_skill_id


async def main():
    client = Client()

    # Before stats
    r = await client.execute("""
        SELECT
          (SELECT COUNT(*) FROM req_skills) as total,
          (SELECT COUNT(*) FROM req_skills WHERE skill_id IS NOT NULL) as resolved,
          (SELECT COUNT(*) FROM candidate_skills) as cand_total,
          (SELECT COUNT(*) FROM candidate_skills WHERE skill_id IS NOT NULL) as cand_resolved
    """)
    bt, br, ct, cr = r.rows[0]
    print(f"BEFORE:")
    print(f"  req_skills:       {br}/{bt} ({100*br/bt:.1f}%)")
    print(f"  candidate_skills: {cr}/{ct} ({100*cr/ct:.1f}%)")
    print()

    # Backfill req_skills
    rs = await client.execute(
        "SELECT id, raw_skill_text FROM req_skills WHERE skill_id IS NULL"
    )
    req_unresolved = list(rs.rows or [])
    req_fixed = 0
    print(f"Resolving {len(req_unresolved)} req_skills rows...")
    for row in req_unresolved:
        rs_id, raw_text = row
        skill_id = await resolve_skill_id(client, raw_text)
        if skill_id:
            await client.execute(
                "UPDATE req_skills SET skill_id = ? WHERE id = ?",
                [skill_id, rs_id],
            )
            req_fixed += 1
            print(f"  + {raw_text!r:50} -> {skill_id}")

    # Backfill candidate_skills
    rs = await client.execute(
        "SELECT id, raw_skill_text FROM candidate_skills WHERE skill_id IS NULL"
    )
    cand_unresolved = list(rs.rows or [])
    cand_fixed = 0
    print(f"\nResolving {len(cand_unresolved)} candidate_skills rows...")
    for row in cand_unresolved:
        cs_id, raw_text = row
        skill_id = await resolve_skill_id(client, raw_text)
        if skill_id:
            await client.execute(
                "UPDATE candidate_skills SET skill_id = ? WHERE id = ?",
                [skill_id, cs_id],
            )
            cand_fixed += 1
            print(f"  + {raw_text!r:50} -> {skill_id}")

    # After stats
    r = await client.execute("""
        SELECT
          (SELECT COUNT(*) FROM req_skills) as total,
          (SELECT COUNT(*) FROM req_skills WHERE skill_id IS NOT NULL) as resolved,
          (SELECT COUNT(*) FROM candidate_skills) as cand_total,
          (SELECT COUNT(*) FROM candidate_skills WHERE skill_id IS NOT NULL) as cand_resolved
    """)
    at, ar, act, acr = r.rows[0]
    print(f"\nAFTER:")
    print(f"  req_skills:       {ar}/{at} ({100*ar/at:.1f}%)  [+{ar-br}]")
    print(f"  candidate_skills: {acr}/{act} ({100*acr/act:.1f}%)  [+{acr-cr}]")
    print(f"  Backfilled: {req_fixed} req_skills + {cand_fixed} candidate_skills")

    await client.close()


if __name__ == '__main__':
    asyncio.run(main())
