#!/usr/bin/python
# -*- coding: UTF-8 -*-
import os
import requests
from requests.auth import HTTPBasicAuth
import json
import logging
import configparser


class frpcManager():
	def __init__(self, api_srv, api_auth=None):
		self.base_srv = api_srv
		self.session = None
		self.auth = api_auth or ('thingsrootadmin', 'Pa88word')
		self.default_frpc = {"admin_addr": "127.0.0.1", "admin_port": "7432", "login_fail_exit": "false",
			"server_addr": "bj.proxy.thingsroot.com", "server_port": "1699", "token": "F^AYnHp29U=M96#o&ESqXB3pL=$)W*qr",
			"protocol": "kcp", "log_file": "./frpc.log"}

	def create_session(self):
		session = requests.session()
		session.auth = HTTPBasicAuth(self.auth[0], self.auth[1])
		session.headers['Accept'] = 'application/json'
		session.headers[
			'User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/57.0.2987.133 Safari/537.36'
		return session

	def local_frpcproxy_status(self, proxy_key):
		proxy = None
		url = 'http://127.0.0.1:7432/api/status'
		headers = {'Accept': 'application/json',
			'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/57.0.2987.133 Safari/537.36'}
		try:
			response = requests.get(url, headers=headers)
			ret_content = json.loads(response.content.decode("utf-8"))
			proxies = ret_content['tcp']
			for pr in proxies:
				if pr['name'] == proxy_key:
					proxy = pr
					break
		except Exception as ex:
			logging.error('query local_proxy_status error:: ' + str(ex))
		return proxy

	def frps_tcpTunnels_get(self, url_path):
		if not self.session:
			self.session = self.create_session()
		try:
			r = self.session.get(self.base_srv + url_path, timeout=3)
			# print("@@@@@@", r.status_code, r.json())
			assert r.status_code == 200
			return r.json()
		except Exception as ex:
			logging.error(ex)
			return None

	def wirte_common_frpcini(self, file, frps_cfg=None):
		if os.access(file, os.F_OK):
			os.remove(file)
		if not frps_cfg:
			frps_cfg = self.default_frpc
		else:
			if frps_cfg.get('server_addr'):
				self.default_frpc['server_addr'] = frps_cfg['server_addr']
			else:
				self.default_frpc['server_addr'] = "bj.proxy.thingsroot.com"
			if frps_cfg.get('server_port'):
				self.default_frpc['server_port'] = frps_cfg['server_port']
			else:
				self.default_frpc['server_port'] = "1699"
			if frps_cfg.get('token'):
				self.default_frpc['token'] = frps_cfg['token']
			else:
				self.default_frpc['token'] = "F^AYnHp29U=M96#o&ESqXB3pL=$)W*qr"
			if frps_cfg.get('protocol'):
				self.default_frpc['protocol'] = frps_cfg['protocol']
			frps_cfg = self.default_frpc
		# print(frps_cfg)
		inicfg = configparser.ConfigParser()
		inicfg.read(file)
		sections = inicfg.sections()
		if "common" not in sections:
			inicfg.add_section("common")
		for k, v in frps_cfg.items():
			inicfg.set("common", k, v)
		inicfg.write(open(file, 'w'))
		return True

	def add_proxycfg_frpcini(self, file, proxycfg):
		inicfg = configparser.ConfigParser()
		inicfg.read(file)
		for k, v in proxycfg.items():
			inicfg.add_section(k)
			for mk, mv in v.items():
				inicfg.set(k, mk, mv)
		inicfg.write(open(file, 'w'))
		return True

	def read_frpcini(self, file):
		frpc = {}
		if os.access(file, os.F_OK):
			inicfg = configparser.ConfigParser()
			inicfg.read(file)
			sections = inicfg.sections()
			for s in sections:
				items = inicfg.items(s)
				d = {}
				for p, q in items:
					d[p] = q
				frpc[s] = d
		return frpc

	def delete_frpcini(self, file):
		if os.access(file, os.F_OK):
			os.remove(file)
			if os.access(file, os.F_OK):
				return False
			else:
				return True
		else:
			return True


if __name__ == '__main__':
	frpapi = frpcManager('http://bj.proxy.thingsroot.com:2699')
	ret = frpapi.frps_tcpTunnels_get('/api/proxy/tcp')
	if ret:
		for r in ret.get('proxies'):
			if r.get('name') == '2-30002-001820-00001_tofreeioerouter':
				proxy_status = r.get('status')
				proxy_online = r.get('cur_conns')
				print(json.dumps(r, sort_keys=False, indent=4, separators=(',', ':')))
