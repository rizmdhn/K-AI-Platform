import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx2 as httpx
from fastmcp import FastMCP
import gateway_metrics as metrics
import per_user_middleware as mw


class MetricTests(unittest.TestCase):
    def test_local_scraper_disables_proxies_and_refuses_redirects(self):
        response=mock.MagicMock()
        response.__enter__.return_value.read.return_value=b'# safe metrics\n'
        opener=mock.Mock();opener.open.return_value=response
        with mock.patch.object(metrics,'build_opener',return_value=opener) as build:
            self.assertEqual(metrics.scrape_local('fixture-token'),'# safe metrics\n')
        handlers=build.call_args.args
        self.assertEqual(handlers[0].proxies,{})
        self.assertIsNone(handlers[1].redirect_request(None,None,302,'redirect',{},'https://outside.invalid'))
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'http://127.0.0.1:8000/metrics')
        self.assertEqual(opener.open.call_args.kwargs['timeout'],5)

    def test_aggregates_only_bounded_labels_and_never_records_identity_or_text(self):
        registry=metrics.Metrics()
        for number in range(100):
            registry.observe({'tool':'arbitrary-'+str(number),'outcome':'sensitive-outcome','error_code':'UNSAFE_SQL',
              'duration_ms':10,'rows':2,'bytes':100,'truncated':True,'ch_user':'secret-user','sql':'secret-query'})
        text=registry.render()
        self.assertIn('kai_mcp_tool_calls_total{tool="other",outcome="sql_denied"} 100',text)
        self.assertIn('kai_mcp_tool_duration_seconds_sum{tool="other",outcome="sql_denied"} 1',text)
        self.assertIn('kai_mcp_returned_rows_total{tool="other",outcome="sql_denied"} 200',text)
        self.assertNotIn('arbitrary-',text)
        self.assertNotIn('secret-',text)
        self.assertNotIn('sensitive-outcome',text)


class MetricHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_route_requires_distinct_scrape_token_and_never_calls_dependencies(self):
        server=FastMCP('metrics-test');mw.setup_middleware(server)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.http_app()),base_url='http://localhost') as client:
            with mock.patch.dict('os.environ',{'KAI_METRICS_TOKEN':''}):
                self.assertEqual((await client.get('/metrics')).status_code,503)
            token='m'*64
            with mock.patch.dict('os.environ',{'KAI_METRICS_TOKEN':token,'CLICKHOUSE_MCP_AUTH_TOKEN':'transport-only'}):
                for headers in ({},{'Authorization':'Bearer transport-only'},{'Authorization':'Bearer incorrect'}):
                    self.assertEqual((await client.get('/metrics',headers=headers)).status_code,401)
                response=await client.get('/metrics',headers={'Authorization':'Bearer '+token})
                self.assertEqual(response.status_code,200);self.assertIn('version=0.0.4',response.headers['content-type'])
                self.assertEqual(response.headers['cache-control'],'no-store');self.assertNotIn(token,response.text)
            with mock.patch.dict('os.environ',{'KAI_METRICS_TOKEN':token,'CLICKHOUSE_MCP_AUTH_TOKEN':token}):
                self.assertEqual((await client.get('/metrics',headers={'Authorization':'Bearer '+token})).status_code,503)

    @mock.patch.dict('os.environ',{'KAI_BINDING_MODE':'manual'})
    async def test_actual_middleware_observes_success_sql_denial_and_cancellation_once(self):
        registry=metrics.Metrics()
        async def call_next(context):
            if context.message.name=='list_tables':raise asyncio.CancelledError()
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({'columns':['n'],'rows':[[1]]}))])
        with mock.patch.object(metrics,'registry',registry),mock.patch.object(mw,'get_http_headers',return_value={'x-ch-user':'fixture','x-ch-password':'fixture-secret'}), \
             mock.patch.object(mw,'get_context',return_value=SimpleNamespace(set_state=mock.AsyncMock())),mock.patch.object(mw.audit,'info'):
            for tool,query in [('run_query','SELECT 1'),('run_query','DROP TABLE private'),('list_tables',None)]:
                context=SimpleNamespace(message=SimpleNamespace(name=tool,arguments={'query':query} if query else {}))
                try:await mw.PerUserClickHouse().on_call_tool(context,call_next)
                except (mw.ToolError,asyncio.CancelledError):pass
        text=registry.render()
        for tool,outcome in [('run_query','ok'),('run_query','sql_denied'),('list_tables','cancelled')]:
            self.assertIn(f'kai_mcp_tool_calls_total{{tool="{tool}",outcome="{outcome}"}} 1',text)
        self.assertNotIn('fixture',text);self.assertNotIn('private',text)
