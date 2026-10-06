import importlib.util
import logging
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

# Source-only unit tests also run on a workstation without AstrBot or aiohttp.
if importlib.util.find_spec('astrbot') is None:
    astrbot = types.ModuleType('astrbot'); astrbot.__path__=[]
    api = types.ModuleType('astrbot.api'); api.logger=logging.getLogger('test')
    sys.modules['astrbot']=astrbot; sys.modules['astrbot.api']=api
if importlib.util.find_spec('aiohttp') is None:
    aiohttp=types.ModuleType('aiohttp')
    aiohttp.ClientError=type('ClientError',(Exception,),{})
    aiohttp.ClientTimeout=lambda **kw:kw
    sys.modules['aiohttp']=aiohttp
spec=importlib.util.spec_from_file_location('qqadmin_official_under_test',Path(__file__).parents[1]/'official.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
GROUP='g'*32; SENDER='s'*32; TARGET='t'*32; BOT='b'*32


def event(*, admin=True, role='member', private=False, mentions=None):
    raw=NS(group_openid=GROUP,raw_data={'group_openid':GROUP,'author': {'member_role':role}, 'mentions': mentions or []})
    return NS(platform_meta=NS(name='qq_official',id='test'),bot=NS(api=None),
              message_obj=NS(raw_message=raw),is_admin=lambda:admin,
              is_private_chat=lambda:private,get_group_id=lambda:GROUP,
              get_sender_id=lambda:SENDER,get_self_id=lambda:BOT,get_message_str=lambda:'/禁言')


class FakeAPI:
    def __init__(self,e):
        self.group=GROUP;self.calls=[];self.responses={};self.role='admin'
    async def request(self, method, endpoint, **kwargs):
        self.calls.append((method,endpoint,kwargs))
        value=self.responses.get((method,endpoint))
        if isinstance(value,Exception):raise value
        if value is not None:return value
        if endpoint=='/bot_state':return {'member_role':self.role}
        if endpoint.startswith('/members/'):
            return {'member_openid':endpoint.split('/')[-1], 'member_role':'member','bot':False}
        if endpoint=='/batch_remove_members':return {'remove_members_result':'success'}
        return {}


class OfficialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.perms={}
        self.plugin=NS(db=NS(get_group_snapshot=lambda g:{'perms':self.perms}))
        self.handler=m.OfficialHandle(self.plugin)
        self.api=FakeAPI(None)
        self.patcher=patch.object(m,'OfficialAPI',lambda e:self.api);self.patcher.start()
    def tearDown(self):self.patcher.stop()
    def mutations(self):return [c for c in self.api.calls if c[0]!='GET']
    async def test_private_never_manages(self):
        result=await self.handler.run('kick',event(private=True),{'target_id':TARGET})
        self.assertIn('私聊',result);self.assertEqual(self.api.calls,[])
    async def test_member_cannot_mute(self):
        result=await self.handler.run('mute',event(admin=False),{'user_id':TARGET,'duration':60,'need_auth':False})
        self.assertIn('权限',result);self.assertEqual(self.mutations(),[])
    async def test_trusted_group_admin(self):
        result=await self.handler.run('mute',event(admin=False,role='admin'),{'user_id':TARGET,'duration':60})
        self.assertIn('禁言 60 秒',result)
    async def test_owner_configuration_stronger(self):
        self.perms['set_group_ban']='群主'
        result=await self.handler.run('mute',event(admin=False,role='admin'),{'user_id':TARGET,'duration':60})
        self.assertIn('群主',result);self.assertFalse(self.mutations())
    async def test_command_preserves_its_original_permission_key(self):
        self.perms.update(set_group_card='群主',set_group_ban='成员')
        result=await self.handler.command('set_group_ban',event(admin=False,role='admin'),
                                          {'user_id':TARGET,'ban_time':60},perm_key='set_group_card')
        self.assertIn('群主',result);self.assertFalse(self.mutations())
    async def test_unknown_identity_fail_closed(self):
        self.api.responses[('GET','/members/'+SENDER)]={'member_role':'admin'}
        result=await self.handler.run('kick',event(admin=False,role=None),{'target_id':TARGET})
        self.assertIn('无法确认',result);self.assertFalse(self.mutations())
    async def test_numeric_qq_rejected(self):
        result=await self.handler.run('mute',event(),{'user_id':'123456789','duration':60})
        self.assertIn('数字 QQ',result);self.assertFalse(self.mutations())
    async def test_bot_cannot_be_target(self):
        result=await self.handler.run('kick',event(),{'target_id':BOT})
        self.assertIn('机器人',result);self.assertFalse(self.mutations())
    async def test_bot_requires_group_admin(self):
        self.api.role='member'
        result=await self.handler.run('mute',event(),{'user_id':TARGET,'duration':60})
        self.assertIn('不是本群管理员',result);self.assertFalse(self.mutations())
    async def test_original_mentions_not_astrbot_at(self):
        e=event(mentions=[{'id':BOT,'is_you':True},{'id':TARGET}])
        e.get_message_str=lambda:'/禁言 <@'+TARGET+'> 60'
        result=await self.handler.command('set_group_ban',e,{'ban_time':60})
        self.assertIn('1 位',result)
        self.assertEqual(self.mutations()[0][2]['payload']['members'][0]['member_openid'],TARGET)
    async def test_unmute_payload(self):
        await self.handler.run('unmute',event(),{'target_id':TARGET})
        payload=self.mutations()[0][2]['payload']['members'][0]
        self.assertEqual(payload,{'op':'del','member_openid':TARGET,'mute_expire_at':''})
    async def test_zero_duration_unmute(self):
        result=await self.handler.run('mute',event(),{'user_id':TARGET,'duration':0})
        self.assertIn('解除禁言',result)
    async def test_invalid_duration_not_clamped(self):
        for duration in [-1,2592001,True,'1.5','NaN','一分钟']:
            with self.subTest(duration=duration):
                self.api.calls.clear()
                result=await self.handler.run('mute',event(),{'user_id':TARGET,'duration':duration})
                self.assertIn('时长',result);self.assertFalse(self.mutations())
    async def test_mute_20_limit(self):
        result=await self.handler.run('mute',event(),{'user_id':','.join([f'{i:032d}' for i in range(21)]),'duration':60})
        self.assertIn('最多操作 20',result);self.assertFalse(self.mutations())
    async def test_kick_admin_never_requested(self):
        self.api.responses[('GET','/members/'+TARGET)]={'member_openid':TARGET,'member_role':'admin','bot':False}
        result=await self.handler.run('kick',event(),{'target_id':TARGET})
        self.assertIn('群主、管理员',result);self.assertFalse(self.mutations())
    async def test_mute_unmute_must_verify_member_identity(self):
        for action in ('mute','unmute'):
            with self.subTest(action=action):
                self.api.calls.clear()
                self.api.responses[('GET','/members/'+TARGET)]={'member_openid':'different','member_role':'member','bot':False}
                result=await self.handler.run(action,event(),{'target_id':TARGET,'duration':60})
                self.assertIn('目标身份未确认',result);self.assertFalse(self.mutations())
    async def test_target_admin_not_muted(self):
        self.api.responses[('GET','/members/'+TARGET)]={'member_openid':TARGET,'member_role':'admin','bot':False}
        await self.handler.run('mute',event(),{'target_id':TARGET,'duration':60});self.assertFalse(self.mutations())
    async def test_kick_missing_bot_flag_fail_closed(self):
        self.api.responses[('GET','/members/'+TARGET)]={'member_openid':TARGET,'member_role':'member'}
        await self.handler.run('kick',event(),{'target_id':TARGET});self.assertFalse(self.mutations())
    async def test_kick_blacklist_failure_not_false_success(self):
        self.api.responses[('POST','/batch_remove_members')]={'remove_members_result':'success','add_to_member_blacklist_fail_openids':[TARGET]}
        result=await self.handler.run('block',event(),{'target_id':TARGET})
        self.assertIn('1 位未成功',result)
    async def test_kick_unknown_result(self):
        self.api.responses[('POST','/batch_remove_members')]={'remove_members_result':'pending'}
        result=await self.handler.run('kick',event(),{'target_id':TARGET})
        self.assertIn('未确认',result)
    async def test_unknown_old_command_not_onebot(self):
        result=await self.handler.command('set_group_card',event(),{})
        self.assertIn('未提供',result);self.assertFalse(self.api.calls)
    async def test_read_respects_original_permission(self):
        self.perms['get_group_member_list']='群主'
        result=await self.handler.run('members',event(admin=False,role='member'))
        self.assertIn('群主',result);self.assertFalse(self.api.calls)
    async def test_read_reports_pagination(self):
        self.api.responses[('GET','/members')]={'members':[{'member_openid':TARGET,'username':'测试','member_role':'member'}],'next_cursor':'more'}
        result=await self.handler.run('members',event())
        self.assertIn('测试',result);self.assertIn('还有更多',result);self.assertFalse(self.mutations())
    async def test_exact_join_request_only(self):
        self.api.responses[('GET','/join_request_list')]={'list':[{'member_openid':TARGET,'join_request_id':'abc'}]}
        result=await self.handler.run('approve',event(),{'extra':TARGET,'request_id':'abc'})
        self.assertIn('接受批准',result)
        self.assertEqual(self.mutations()[0][2]['payload'],{'op':'approve','join_request_id':'abc'})
    async def test_missing_join_request_never_approved(self):
        result=await self.handler.run('approve',event(),{'extra':TARGET,'request_id':'abc'})
        self.assertIn('没有这条',result);self.assertFalse(self.mutations())
    async def test_unblock_only_actual_group_blacklist_target(self):
        result=await self.handler.run('unblock',event(),{'member_id':TARGET})
        self.assertIn('没有这个成员',result);self.assertFalse(self.mutations())
        self.api.responses[('GET','/member_blacklist')]={'users':[{'member_openid':TARGET}]}
        result=await self.handler.run('unblock',event(),{'member_id':TARGET})
        self.assertIn('接受解除',result);self.assertEqual(len(self.mutations()),1)
    async def test_status_read_only_reports_every_api(self):
        self.api.responses[('GET','/members')]=m.OfficialError('尚未开通接口（11253）',code=11253)
        result=await self.handler.run('status',event(admin=False))
        self.assertIn('11253',result);self.assertIn('机器人身份',result);self.assertFalse(self.mutations())
    async def test_llm_false_need_auth_never_bypasses(self):
        reached=[]
        async def llm_set_group_ban(plugin,event,user_id,duration,need_auth=True):
            reached.append(1);yield 'OneBot'
        wrapped=m.official_tool(llm_set_group_ban)
        self.plugin.official=self.handler
        items=[x async for x in wrapped(self.plugin,event(admin=False),TARGET,60,False)]
        self.assertIn('权限',items[0]);self.assertEqual(reached,[]);self.assertFalse(self.mutations())
    async def test_llm_onebot_keeps_original(self):
        async def llm_set_group_ban(plugin,event,user_id,duration,need_auth=True):yield 'OneBot'
        e=event();e.platform_meta.name='aiocqhttp'
        items=[x async for x in m.official_tool(llm_set_group_ban)(self.plugin,e,123,60)]
        self.assertEqual(items,['OneBot']);self.assertFalse(self.api.calls)


class APIResponse:
    def __init__(self,status,data):self.status=status;self.data=data;self.content_length=100
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def json(self,**kwargs):return self.data


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def call(self,status,data,method='GET'):
        calls=[]
        async def check():pass
        response=APIResponse(status,data)
        session=NS(request=lambda *a,**kw: (calls.append((a,kw)) or response))
        http=NS(check_session=check,_headers={'Authorization':'private-token'},_session=session,is_sandbox=False)
        e=event();e.bot=NS(api=NS(_http=http))
        class Route:
            def __init__(self,method,path):self.url='https://api.sgroup.qq.com'+path
        httpmodule=types.ModuleType('botpy.http');httpmodule.Route=Route
        with patch.dict(sys.modules,{'botpy.http':httpmodule}):
            try:result=await m.OfficialAPI(e).request(method,'/members')
            except m.OfficialError as error:result=error
        return result,calls
    async def test_whitelist_error_code_survives(self):
        result,calls=await self.call(400,{'code':11253,'message':'raw private-token response'})
        self.assertEqual(result.code,11253);self.assertIn('开通',str(result))
        self.assertNotIn('private-token',str(result));self.assertEqual(len(calls),1)
    async def test_admin_error_has_explanation(self):
        result,calls=await self.call(400,{'code':11703})
        self.assertIn('管理员',str(result));self.assertEqual(len(calls),1)
    async def test_payload_error_even_on_http_200(self):
        result,_=await self.call(200,{'code':11253})
        self.assertIsInstance(result,m.OfficialError)
    async def test_empty_200_never_claims_success(self):
        result,_=await self.call(200,None,'POST')
        self.assertIsInstance(result,m.OfficialError);self.assertIn('未返回',str(result))
    async def test_redirect_never_claims_success(self):
        result,_=await self.call(302,{},'POST')
        self.assertIsInstance(result,m.OfficialError)
    async def test_success_and_no_redirect(self):
        result,calls=await self.call(200,{'members':[]})
        self.assertEqual(result,{'members':[]});self.assertFalse(calls[0][1]['allow_redirects'])
        self.assertTrue(calls[0][0][1].startswith('https://api.bot.qq.com/'))
    async def test_no_mutation_retry(self):
        result,calls=await self.call(500,{'code':1},'POST')
        self.assertIsInstance(result,m.OfficialError);self.assertEqual(len(calls),1)
    async def test_channel_id_not_group_openid(self):
        e=event();e.message_obj.raw_message=NS(raw_data={})
        with self.assertRaises(m.OfficialError):m.OfficialAPI(e)


if __name__=='__main__':unittest.main()
