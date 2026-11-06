"""Offline synthetic dataset checks/context export. No model or warehouse execution."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
import time

IDENTIFIER=re.compile(r'[a-z][a-z0-9_]*')
TYPES={'String':'TEXT','Date':'TEXT','UInt32':'INTEGER','Int64':'INTEGER'}


def visible_documents(fixtures,persona):
    active=[doc for doc in fixtures['documents'] if doc['status']=='active' and not doc.get('duplicate_of')]
    sources=[doc['source'] for doc in active]
    if len(sources)!=len(set(sources)):raise ValueError('Multiple current versions of one source')
    return [doc for doc in active if persona in doc['acl']]


def context(fixtures,case):
    persona=case['persona'];permissions=fixtures['personas'][persona]['tables']
    schema=[]
    for name,policy in sorted(permissions.items()):
        table=fixtures['tables'][name]
        if table['relationships']!=[]:raise ValueError('Relationship metadata needs a reviewed typed access contract')
        schema.append({'table':name,'description':table['description'],
          'columns':[column for column in table['columns'] if column['name'] in policy['columns']],
          'relationships':table['relationships'],'row_regions':policy.get('regions'),
          'provenance':'synthetic_draft'})
    result={'question':case['question'],'fixture_persona':persona,'schema':schema,
      'documents':[{'id':doc['id'],'source':doc['source'],'version':doc['version'],'text':doc['text'],'untrusted':True}
                   for doc in visible_documents(fixtures,persona)]}
    if 'observation' in case:result['tool_observation']=case['observation']
    return result


def oracle(fixtures,persona,sql):
    if not isinstance(sql,str) or len(sql.encode())>8192:raise ValueError('Invalid reference query')
    connection=sqlite3.connect(':memory:');allowed=set();databases=set()
    try:
        for name,policy in fixtures['personas'][persona]['tables'].items():
            database,table=name.split('.')
            columns=policy['columns'];source=fixtures['tables'][name]
            if not all(IDENTIFIER.fullmatch(value) for value in [database,table,*columns]):raise ValueError('Invalid fixture identifier')
            if database not in databases:
                connection.execute(f'ATTACH DATABASE \':memory:\' AS "{database}"');databases.add(database)
            types={column['name']:TYPES[column['type']] for column in source['columns']}
            definitions=','.join(f'"{column}" {types[column]}' for column in columns)
            connection.execute(f'CREATE TABLE "{database}"."{table}" ({definitions})')
            rows=source['rows']
            if source.get('row_scope_key'):
                rows=[row for row in rows if row[source['row_scope_key']] in policy['regions']]
            connection.executemany(f'INSERT INTO "{database}"."{table}" VALUES ({",".join("?" for _ in columns)})',
                                   [[row[column] for column in columns] for row in rows])
            allowed.add(name)
        def authorize(action,arg1,arg2,database,trigger):
            if action==sqlite3.SQLITE_SELECT:return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_READ and f'{database}.{arg1}' in allowed:return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_FUNCTION and arg2.lower() in {'sum','count','min','max','avg'}:return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize)
        deadline=time.monotonic()+0.25
        connection.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        rows=connection.execute(sql).fetchmany(1001)
        if len(rows)>1000:raise ValueError('Reference output too large')
        return [list(row) for row in rows]
    except (sqlite3.Error,KeyError,TypeError):
        raise ValueError('Reference query is invalid or exceeds fixture permissions') from None
    finally:
        connection.close()


def validate(fixtures,corpus):
    try:
        if fixtures['version']!=1 or corpus['version']!=1 or fixtures['synthetic'] is not True or corpus['synthetic'] is not True:
            raise ValueError('Only the versioned synthetic corpus is supported')
        if fixtures['review_state']!='synthetic_draft' or corpus['review_state']!='synthetic_draft':
            raise ValueError('Do not claim client review from this corpus')
        personas=fixtures['personas'];tables=fixtures['tables']
        for name,table in tables.items():
            if table['relationships']!=[]:raise ValueError('Relationship metadata needs a reviewed typed access contract')
            if len(name.split('.'))!=2 or not all(IDENTIFIER.fullmatch(v) for v in name.split('.')):raise ValueError('Invalid table')
            columns=[column['name'] for column in table['columns']]
            if len(columns)!=len(set(columns)) or not 1<=len(columns)<=64 or len(table['rows'])>1000:raise ValueError('Invalid fixture size')
            for column in table['columns']:
                if not IDENTIFIER.fullmatch(column['name']) or column['type'] not in TYPES or type(column['nullable']) is not bool:raise ValueError('Invalid column')
            if any(set(row)!=set(columns) for row in table['rows']):raise ValueError('Invalid fixture row')
        for persona in personas.values():
            for name,policy in persona['tables'].items():
                available={column['name'] for column in tables[name]['columns']}
                if not policy['columns'] or len(policy['columns'])!=len(set(policy['columns'])) or not set(policy['columns'])<=available:raise ValueError('Invalid column partition')
                if tables[name].get('row_scope_key') and (not isinstance(policy.get('regions'),list) or not policy['regions']):raise ValueError('Missing row partition')
        documents=fixtures['documents'];ids=[doc['id'] for doc in documents]
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate document IDs')
        by_id={doc['id']:doc for doc in documents}
        for doc in documents:
            if doc['status'] not in {'active','superseded','revoked'} or not set(doc['acl'])<=set(personas):raise ValueError('Invalid document ACL/status')
            if type(doc['version']) is not int or doc['version']<1 or not isinstance(doc['text'],str):raise ValueError('Invalid evidence')
            if doc.get('duplicate_of'):
                canonical=by_id[doc['duplicate_of']]
                if canonical.get('duplicate_of') or canonical['id']==doc['id'] or any(doc[k]!=canonical[k] for k in ['source','version','text','acl','status']):raise ValueError('Invalid duplicate evidence')
        # Enforce one current canonical source even when no answer case cites that source yet.
        visible_documents(fixtures,next(iter(personas)))
        cases=corpus['cases'];case_ids=[case['id'] for case in cases]
        if len(case_ids)!=len(set(case_ids)) or not cases:raise ValueError('Duplicate or empty cases')
        for case in cases:
            if not re.fullmatch(r'[a-z0-9-]+',case['id']) or case['persona'] not in personas or case['domain'] not in {'sql','metadata','documents'}:raise ValueError('Invalid case identity')
            if not isinstance(case['question'],str) or not case['question'].strip() or not case['rubric'] or not case['tags']:raise ValueError('Incomplete case')
            expected=case['expected'];action=expected['action']
            if action not in {'query','deny','clarify','unknown','answer','controlled_failure'}:raise ValueError('Invalid expected action')
            if action=='query':
                if case['domain']!='sql' or oracle(fixtures,case['persona'],expected['reference_sql'])!=expected['rows']:raise ValueError('Wrong SQL ground truth')
                # AI function cases (AF-01): one expected label per returned row, keyed by its first column.
                if 'labels' in expected and set(expected['labels'])!={str(row[0]) for row in expected['rows']}:raise ValueError('Labels do not match the returned rows')
            if action=='answer' and case['domain']=='metadata':
                field=expected['field'];table=field['table']
                if table not in personas[case['persona']]['tables'] or field['column'] not in personas[case['persona']]['tables'][table]['columns']:raise ValueError('Unauthorized metadata evidence')
                column=next(c for c in tables[table]['columns'] if c['name']==field['column'])
                if column[field['name']]!=field['value']:raise ValueError('Wrong metadata ground truth')
            if action=='answer' and case['domain']=='documents':
                visible={doc['id']:doc for doc in visible_documents(fixtures,case['persona'])}
                citations=expected['citations']
                if not citations or len(citations)!=len(set(citations)) or not set(citations)<=set(visible):raise ValueError('Unauthorized/stale/duplicate evidence')
                evidence=' '.join(visible[id]['text'] for id in citations)
                if not expected['answer_contains'] or any(phrase not in evidence for phrase in expected['answer_contains']):raise ValueError('Unsupported answer evidence')
        return {'cases':len(cases),'domains':dict(Counter(case['domain'] for case in cases)),
                'actions':dict(Counter(case['expected']['action'] for case in cases)),
                'fixture_validation':'passed','model_evaluated':False,'production_authorization_evaluated':False}
    except (KeyError,TypeError,StopIteration,sqlite3.Error):
        raise ValueError('Invalid fixture/case structure or reference') from None


def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON key')
        result[key]=value
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',help='Export only the selected persona-filtered model context')
    args=parser.parse_args();root=Path(__file__).resolve().parent
    try:
        fixtures=json.loads((root/'fixtures.json').read_text(),object_pairs_hook=unique)
        corpus=json.loads((root/'cases.json').read_text(),object_pairs_hook=unique)
        report=validate(fixtures,corpus)
        if args.case:report=context(fixtures,next(case for case in corpus['cases'] if case['id']==args.case))
        print(json.dumps(report,ensure_ascii=False,indent=2))
    except (OSError,ValueError,StopIteration):
        parser.exit(1,'Evaluation dataset or case is invalid; no model/warehouse request made.\n')
