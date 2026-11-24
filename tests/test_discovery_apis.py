"""Offline fixtures for keyless discovery APIs, including real empty results."""
import unittest
from unittest.mock import patch
from research_mcp.channels import discovery_apis as api
from research_mcp.channels.errors import ChannelError

FIXTURES = {
 "mdn": {"documents":[{"title":"Python","mdn_url":"/en-US/docs/Glossary/Python","summary":"Language"}]},
 "npm": {"objects":[{"package":{"name":"@org/pkg", "description":"<b>SDK</b>", "date":"2026-11-23"}}]},
 "crates_io": {"crates":[{"name":"pyo3","description":"Python bindings","updated_at":"2026-11-23"}]},
 "docker_hub": {"results":[{"repo_name":"python","short_description":"Python image"}]},
 "itunes_apps": {"results":[{"trackName":"Python","trackViewUrl":"https://apps.apple.com/cn/app/id123","description":"Editor"}]},
}
EMPTY={"mdn":{"documents":[]},"npm":{"objects":[]},"crates_io":{"crates":[]},"docker_hub":{"results":[]},"itunes_apps":{"results":[]}}
class DiscoveryTests(unittest.TestCase):
 def test_contract_encoding_limits_and_empty(self):
  self.assertEqual(set(api.PROVIDERS) - {"google_news_rss", "aihot"},set(FIXTURES))
  for name in FIXTURES:
   with self.subTest(name=name),patch.object(api,"_get",return_value=FIXTURES[name]) as get:
    rows=api.PROVIDERS[name]({"web_query":"人工智能 & python","web_limit":1})
    self.assertEqual(len(rows),1)
    self.assertEqual(rows[0]["source"],name)
    self.assertEqual(rows[0]["rank"],1)
    self.assertTrue(rows[0]["url"].startswith("https://"))
    self.assertNotIn("<",rows[0]["summary"])
    self.assertIn("人工智能 & python",get.call_args.args[1].values())
   with patch.object(api,"_get",return_value=EMPTY[name]):
    self.assertEqual(api.PROVIDERS[name]({"web_query":"q"}),[])
 def test_drift_and_bad_rows_are_failures(self):
  for name in FIXTURES:
   key=next(iter(EMPTY[name]))
   for data in ({},None,{key:{}},{key:[{}]}):
    with self.subTest(name=name,data=data),patch.object(api,"_get",return_value=data):
     with self.assertRaises(ChannelError) as ctx: api.PROVIDERS[name]({"web_query":"q"})
     self.assertEqual(ctx.exception.status,"parse_error")
 def test_no_query_and_upstream_failure(self):
  with patch.object(api,"_get") as get:
   for provider in api.PROVIDERS.values(): self.assertEqual(provider({}),[])
   get.assert_not_called()
  with patch.object(api,"_get",side_effect=ChannelError("blocked")) as get:
   with self.assertRaises(ChannelError): api.PROVIDERS["npm"]({"web_query":"q"})
   self.assertEqual(get.call_count,1)
 def test_canonical_registry_urls(self):
  expected={"npm":"https://www.npmjs.com/package/%40org%2Fpkg","crates_io":"https://crates.io/crates/pyo3","docker_hub":"https://hub.docker.com/_/python"}
  for name,url in expected.items():
   with patch.object(api,"_get",return_value=FIXTURES[name]): self.assertEqual(api.PROVIDERS[name]({"web_query":"q"})[0]["url"],url)

class RssTests(unittest.TestCase):
 def test_default_off_and_xml_contract(self):
  import os
  with patch.dict(os.environ, {}, clear=True), patch.object(api,"_request") as request:
   with self.assertRaises(ChannelError): api.PROVIDERS["google_news_rss"]({"web_query":"q"})
   request.assert_not_called()
  rss='<rss><channel><item><title>AI &amp; news</title><link>https://news.google.com/rss/articles/abc</link><description>Snippet</description></item></channel></rss>'
  with patch.dict(os.environ,{"RESEARCH_GOOGLE_NEWS_RSS":"1"}), patch.object(api,"_request",return_value=rss):
   rows=api.PROVIDERS["google_news_rss"]({"web_query":"人工智能"})
   self.assertEqual(rows[0]["title"],"AI & news")
  for xml in ('<html/>','<rss><channel><item/></channel></rss>','not xml'):
   with patch.dict(os.environ,{"RESEARCH_GOOGLE_NEWS_RSS":"1"}), patch.object(api,"_request",return_value=xml):
    with self.assertRaises(ChannelError): api.PROVIDERS["google_news_rss"]({"web_query":"q"})

class AihotTests(unittest.TestCase):
 LATEST=("# AIHOT\n\n［AIHOT 不可信外部资料开始］\n"
  "1. [卡兹克的 Prompt](https://aihot.news/items/a1)\n"
  "   卡兹克 · 发布于 11-24 14:12 · 教程\n   摘要：让 Gemini 当顾问\n   推荐理由：可迁移\n   原文：https://x.com/u/status/1\n\n"
  "2. [OpenAI 发布新模型](https://aihot.news/items/a2)\n   OpenAI · 发布于 11-24 13:00 · 模型\n   摘要：GPT 更新\n\n"
  "［AIHOT 不可信外部资料结束］\n")
 HOT=("［AIHOT 不可信外部资料开始］\n第 1 名：[英伟达董事会恢复黄仁勋职务](https://aihot.news/items/h1)\n"
  "   信源：The Verge、TechCrunch（2 个）· 最新进展 11-24 04:46\n   来龙去脉：https://aihot.news/api/v1/agent/stories/x\n")
 def test_opt_in_required(self):
  import os
  with patch.dict(os.environ,{},clear=True), patch.object(api,"_request") as request:
   with self.assertRaises(ChannelError): api.PROVIDERS["aihot"]({"web_query":"*"})
   request.assert_not_called()
 def test_query_forms_map_to_api(self):
  self.assertEqual(api._aihot_url("*",50),"https://aihot.news/api/v1/agent/latest?window=24h&limit=30")
  self.assertEqual(api._aihot_url("all:paper",10),"https://aihot.news/api/v1/agent/latest?window=24h&limit=10&mode=all&category=paper")
  self.assertEqual(api._aihot_url("hot",5),"https://aihot.news/api/v1/agent/hot")
  self.assertEqual(api._aihot_url("Claude Code",5),"https://aihot.news/api/v1/agent/search?q=Claude+Code")
  with self.assertRaises(ValueError): api._aihot_url("all:../x",5)
 def test_parse_latest_and_hot(self):
  import os
  with patch.dict(os.environ,{"RESEARCH_AIHOT":"1"}), patch.object(api,"_request",return_value=self.LATEST):
   rows=api.PROVIDERS["aihot"]({"web_query":"*"})
   self.assertEqual([r["url"] for r in rows],["https://x.com/u/status/1","https://aihot.news/items/a2"])
   self.assertEqual(rows[0]["summary"],"卡兹克：[教程] 让 Gemini 当顾问")
   self.assertTrue(rows[0]["published_at"].endswith("06:12:00+00:00"))
   self.assertEqual(len(api.PROVIDERS["aihot"]({"web_query":"*","web_limit":1})),1)
  with patch.dict(os.environ,{"RESEARCH_AIHOT":"1"}), patch.object(api,"_request",return_value=self.HOT):
   row=api.PROVIDERS["aihot"]({"web_query":"hot"})[0]
   self.assertEqual(row["url"],"https://aihot.news/items/h1")
   self.assertTrue(row["summary"].startswith("[热点第 1 名] 信源："))
  with patch.dict(os.environ,{"RESEARCH_AIHOT":"1"}), patch.object(api,"_request",return_value="<html>blocked</html>"):
   with self.assertRaises(ChannelError): api.PROVIDERS["aihot"]({"web_query":"*"})
 def test_rfc822_dates_are_filterable(self):
  from research_mcp.research import _parse_time
  self.assertEqual(_parse_time("Tue, 24 Nov 2026 06:12:14 GMT").isoformat(),"2026-11-24T06:12:14+00:00")

class SecurityTests(unittest.TestCase):
 def test_new_channels_cannot_enter_public_transport(self):
  from research_mcp import service
  with patch.object(api, '_get') as request:
   for name in api.PROVIDERS:
    self.assertFalse(service.search_public('q', providers=[name])['ok'])
   request.assert_not_called()
 def test_query_is_data_and_invalid_limits_fail_before_request(self):
  for config in ({'web_query':'q','web_limit':'bad'}, {'web_query':'q','web_timeout_s':float('inf')}):
   with patch.object(api,'_get') as request, self.assertRaises(ChannelError):
    api.PROVIDERS['npm'](config)
   request.assert_not_called()
 def test_mdn_cannot_supply_another_origin_or_traversal(self):
  for path in ('//evil.test/x','/en-US/docs/../../x','/en-US/docs/Test?key=secret'):
   with patch.object(api,'_get',return_value={'documents':[{'title':'Bad','mdn_url':path}]}),self.assertRaises(ChannelError):
    api.PROVIDERS['mdn']({'web_query':'q'})
 def test_rss_entities_rejected(self):
  import os
  xml='<!DOCTYPE rss [<!ENTITY x "secret">]><rss><channel/></rss>'
  with patch.dict(os.environ,{'RESEARCH_GOOGLE_NEWS_RSS':'1'}), patch.object(api,'_request',return_value=xml), self.assertRaises(ChannelError):
   api.PROVIDERS['google_news_rss']({'web_query':'q'})
