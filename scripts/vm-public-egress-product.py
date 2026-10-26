#!/usr/bin/env python3
"""Task public-only product records and exact existing authorization inventory."""
import datetime,fcntl,json,os,pathlib,runpy,urllib.parse
RUN=pathlib.Path('/home/chabking/workspace/ani-resource-service-runs/vm-mvp-20261023-01a168ce')
def main():
 assert os.environ.get('SSH_CONNECTION','').split()[2:3]==['172.16.101.31']
 with pathlib.Path('/home/chabking/workspace/ani-network-service-runs/net05a-heavy.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  gov=runpy.run_path(str(RUN/'gov-candidate.py'))
  creds=json.loads((RUN/'private/gov-candidate/credentials.json').read_text())
  private_dsn=urllib.parse.urlsplit((RUN/'private/resource-candidate/owner.dsn').read_text().strip())
  database=private_dsn.path.lstrip('/');owner=private_dsn.username
  assert database=='vm_resource_candidate_01a168ce'
  query="""BEGIN READ ONLY;
SELECT jsonb_build_object('database',current_database(),
'platform',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',resource_id,'kind',kind,'name',name,'state',state,'reason',reason,'version',version,'provider_name',provider_name,'provider_uid',provider_uid,'binding_id',binding_id)) FROM network_platform_resources WHERE kind IN ('device','vlan','egress_gateway','public_pool')),'[]'::jsonb),
'public_pools',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',resource_id,'mode',mode,'cidr',cidr,'gateway_id',gateway_id,'vlan_id',vlan_network_id,'ovn_gateway_ip',ovn_gateway_ip,'upstream_gateway_ip',upstream_gateway_ip,'allocation_enabled',allocation_enabled,'revision',config_revision,'verification_expires_at',verification_expires_at)) FROM network_public_pools WHERE scope='public'),'[]'::jsonb),
'default_public_pools',COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM network_default_public_pools x),'[]'::jsonb),
'default_egresses',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',resource_id,'state',state,'phase',phase,'pool_id',pool_id,'address',address,'provider_eip',eip_provider_name,'provider_eip_uid',eip_provider_uid,'provider_snat',snat_provider_name,'provider_snat_uid',snat_provider_uid)) FROM network_default_vpc_egresses),'[]'::jsonb),
'virtual_switches',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',resource_id,'name',name,'state',state,'binding_state',binding_state,'cluster_id',cluster_id)) FROM network_virtual_switches),'[]'::jsonb),
'public_eips',COALESCE((SELECT jsonb_agg(jsonb_build_object('tenant_id',tenant_id,'id',eip_id,'state',state,'address',address,'pool_id',pool_id)) FROM network_eips WHERE scope='public'),'[]'::jsonb),
'public_snats',COALESCE((SELECT jsonb_agg(jsonb_build_object('tenant_id',tenant_id,'id',snat_id,'state',state,'vpc_id',vpc_id,'eip_id',eip_id,'desired_enabled',desired_enabled,'applied_enabled',applied_enabled)) FROM network_snat_bindings WHERE purpose='public'),'[]'::jsonb));
COMMIT;"""
  records=json.loads(gov['psql'](owner,query,database=database))
  token=gov['api']('POST','/api/v1/auth/platform/password/login',{'username':creds['platform_username'],'password':creds['platform_password']})['access_token']
  me=gov['api']('GET','/api/v1/admin/me',token=token)
  assert int(me.get('tenantId',0))==0 and me['username']==creds['platform_username']
  result={'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'resource_candidate_database':records,'platform_principal':{'user_id':me.get('id'),'tenant_id':me.get('tenantId'),'username':me['username']},'platform_gets':{},'mutations':0,'login_refresh_only':1}
  paths=['/api/v1/admin/networks/virtual-switches','/api/v1/admin/networks/vlan-networks','/api/v1/admin/networks/egress-gateways','/api/v1/admin/networks/public-address-pools','/api/v1/admin/networks/capabilities']
  for path in paths:
   try:result['platform_gets'][path]={'status':200,'response':gov['api']('GET',path,token=token)}
   except gov['ProductAPIError'] as error:result['platform_gets'][path]={'status':error.status,'reason':error.reason}
  permissions=gov['api']('GET','/api/v1/admin/permissions?noPaging=true',token=token).get('items',[])
  roles=gov['api']('GET','/api/v1/admin/roles?noPaging=true',token=token).get('items',[])
  role=next(r for r in roles if r.get('code')=='platform:admin' and int(r.get('tenantId',0))==0)
  role=gov['api']('GET','/api/v1/admin/roles/'+str(role['id']),token=token)
  granted={int(x) for x in role.get('permissions',[])}
  result['public_permissions']=[{'id':p['id'],'code':p['code'],'platform_role_granted':int(p['id']) in granted} for p in permissions if p.get('code','').startswith(('network:platform:','network:eip:','network:snat:')) and 'intranet' not in p.get('code','')]
  tenant=gov['api']('POST','/api/v1/auth/password/login',{'tenant_name':creds['tenant_code'],'username':creds['tenant_username'],'password':creds['tenant_password']})['access_token']
  result['tenant_gets']={}
  for path in ['/api/v1/networks/eips','/api/v1/networks/vpcs/vpc_e413becb1f4a46468f058e32f33a6305/snat']:
   try:result['tenant_gets'][path]={'status':200,'response':gov['api']('GET',path,token=tenant)}
   except gov['ProductAPIError'] as error:result['tenant_gets'][path]={'status':error.status,'reason':error.reason}
  path=RUN/'evidence/vm-public-egress-product-inventory.json';gov['save'](path,result)
  print(json.dumps({'result':'READ_ONLY_COMPLETE','receipt':str(path),'platform_count':len(records['platform']),'public_pool_count':len(records['public_pools']),'platform_gets':{p:v['status'] for p,v in result['platform_gets'].items()},'tenant_gets':{p:v['status'] for p,v in result['tenant_gets'].items()},'mutations':0}))
if __name__=='__main__':main()
