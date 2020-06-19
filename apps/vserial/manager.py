import threading
import logging
import time
import json
import requests
import hashlib
import base64
from cores.log import configure_logger
from apps.vserial.tcp_client_h import TcpClientHander
from serial.tools.list_ports import comports
from apps.vserial.vs_port import VSPort
from helper import APPCtrl
from helper.frpcManager import frpcManager
from helper.thingscloud import CloudApiv1

sn_model_map = {"TRTX01": "C202", "2-30002": "Q102", "2-30100": "Q204", "2-30102": "Q204"}
model_port_map = {
    "Q102": {"com1": "/dev/ttymxc1", "com2": "/dev/ttymxc2"},
    "Q204": {"com1": "/dev/ttymxc1", "com2": "/dev/ttymxc2", "com3": "/dev/ttymxc3", "com4": "/dev/ttymxc4"},
    "C202": {"com1": "/dev/ttyS1", "com2": "/dev/ttyS2"}
}


class VSPAXManager(threading.Thread):
    def __init__(self, appname, stream_pub):
        threading.Thread.__init__(self)
        self._appname = appname
        self._ports = []
        self._thread_stop = False
        self._mqtt_stream_pub = stream_pub
        self._enable_heartbeat = APPCtrl().get_heartbeat()
        self.TRAccesskey = None
        self.TRCloudapi = None
        self.frps_host = None
        self.frps_port = None
        self.frps_token = None
        self.FRPApi = None
        self.userinfo = {"name": None, "gate": None, "client_online": None, "tunnel_host": None, "token": None,
                         "tunnel_port": None, "tunnel_online": None, "gate_com_params": None, "gate_status": None,
                         "gate_frpc_status": None, "gate_port_name": None, "local_port_name": None, "info": None}
        self._gate_online = False
        self._gate_frpc_is_running = False
        self._vserial_is_running = False
        self._start_time = None
        self._stop_time = None
        self._heartbeat_timeout = time.time() + 90
        self._vsport_ctrl = None
        self._log = configure_logger('default', 'logs/service.log')

    def list(self):
        return [handler.as_dict() for handler in self._ports]

    def list_ports(self):
        return [handler.get_port_key() for handler in self._ports]

    def list_all(self):
        phy_com = [c[0] for c in comports()]
        vir_com = self._vsport_ctrl.ListVir()
        for v in vir_com:
            if v not in phy_com:
                phy_com.append(v)
            pass
        return phy_com

    def list_vir(self):
        return self._vsport_ctrl.ListVir()

    def reset_bus(self):
        return self._vsport_ctrl.ResetBus()

    def get(self, name):
        for handler in self._ports:
            if handler.is_port(name):
                return handler
        return None

    def add(self, port):
        port.set_stream_pub(self._mqtt_stream_pub)
        port.start()
        self._ports.append(port)

        return True

    def remove(self, name):
        port = self.get(name)
        if not port:
            logging.error("Failed to find port {0}!!".format(name))
            return False
        port.stop()
        self._ports.remove(port)
        return True

    def info(self, name):
        handler = self.get(name)
        if not handler:
            logging.error("Failed to find port {0}!!".format(name))
            return False
        return handler.as_dict()

    def gate_vserial_data(self):
        data = self.TRCloudapi.get_device_data(self.userinfo['gate'], self.userinfo['gate'] + ".freeioe_Vserial_frpc")
        if data:
            try:
                rawdata = data['message']
                # print(json.dumps(rawdata, sort_keys=False, indent=4, separators=(',', ':')))
                if rawdata:
                    if rawdata.get("frpc_status").get("PV") == "running":
                        self.userinfo["gate_frpc_status"] = True
                        self._gate_frpc_is_running = True
                    else:
                        self.userinfo["gate_frpc_status"] = False
                        self._gate_frpc_is_running = False
                    if rawdata.get("current_com_params").get("PV") != "":
                        self.userinfo["gate_com_params"] = json.loads(rawdata.get("current_com_params").get("PV"))
                    if rawdata.get("com_to_frps_mapport").get("PV") != "":
                        peer = rawdata.get("com_to_frps_mapport").get("PV")
                        self.userinfo["tunnel_port"] = peer.split(':')[1]
            except Exception as ex:
                self._log.exception(ex)

    def start_vserial(self):
        if not self._vserial_is_running:
            if not self.TRCloudapi:
                self.TRCloudapi = CloudApiv1(self.TRAccesskey)
            self.enable_heartbeat(True, 60)
            # 检测网关是否在线
            gate_status_ret = self.TRCloudapi.get_gate_status(self.userinfo['gate'])
            if gate_status_ret:
                if gate_status_ret['message'] == "ONLINE":
                    self._gate_online = True
                    self.userinfo['gate_status'] = "ONLINE"
                else:
                    self._gate_online = False
                    self.userinfo['gate_status'] = "OFFLINE"
            if self._gate_online:
                model = sn_model_map.get(self.userinfo.get("gate")[0:6]) or sn_model_map.get(
                    self.userinfo.get("gate")[0:7]) or "C202"
                gate_port = model_port_map.get(model).get(self.userinfo.get("gate_port_name")) or "/dev/ttyS1"
                gate_vserial_command = {"port": gate_port, "frps": {"server_addr": self.userinfo['tunnel_host'],
                                        "server_port": self.frps_port or '1699',
                                        "token": self.frps_token or 'F^AYnHp29U=M96#o&ESqXB3pL=$)W*qr'},
                                        "user_id": self.userinfo['name']}
                gate_datas = {"id": self.userinfo['gate'] + '/send_command/start/' + str(time.time()),
                              "device": self.userinfo['gate'],
                              "data": {"device": self.userinfo['gate'] + ".freeioe_Vserial_frpc",
                                       "cmd": "start",
                                       "param": gate_vserial_command}}
                ret, ret_content = self.TRCloudapi.post_command_to_cloud(gate_datas)
                # print(json.dumps(ret, sort_keys=False, indent=4, separators=(',', ':')))
                if ret:
                    if ret_content["gate_mes"]["result"]:
                        for i in range(3):
                            self._log.info(str(i) + ' query vserial_port!')
                            self.gate_vserial_data()
                            if self.userinfo.get("tunnel_port"):
                                break
                            time.sleep(i + 2)
                        if self.userinfo.get("tunnel_port"):
                            local_ports = self.list_all()
                            local_newPort = None
                            for x in range(0, len(local_ports) + 1):
                                local_newPort = "COM" + str(x+1)
                                if local_newPort not in local_ports:
                                    break
                            self.userinfo["local_port_name"] = local_newPort
                            self._vserial_is_running = True
                            self._start_time = time.time()
                            self.userinfo["info"] = {"user": self.userinfo.get("name"),
                                                     "gate": self.userinfo.get("gate"),
                                                     "gate_port": self.userinfo.get("gate_port_name"),
                                                     "serial_driver": "vspax"}
                            handler = TcpClientHander(self.userinfo.get("local_port_name"),
                                                      self.userinfo.get("tunnel_host"),
                                                      int(self.userinfo.get("tunnel_port")),
                                                      self.userinfo.get("info"))
                            self.add(handler)
                            return self._vserial_is_running, self.userinfo
                        else:
                            return False, "无法获取网关串口映射到FRPS的端口，请检查网关是否开启数据上传"
                    else:
                        return False, "下发指令到网关不正常，请检查后重试"
                else:
                    return False, "网关frpc服务启动不正常，请检查后重试"
            else:
                return False, "网关不在线，或你无权访问此网关，请检查后重试"
        else:
            return False, "用户 {0} 正在使用中……，如需重新配置，请先停止再启动".format(self.userinfo.get("name"))

    def stop_vserial(self):
        ret1 = None
        if self._vserial_is_running:
            if self._gate_online:
                gate_datas = {"id": self.userinfo['gate'] + '/send_command/stop/' + str(time.time()),
                              "device": self.userinfo['gate'],
                              "data": {"device": self.userinfo['gate'] + ".freeioe_Vserial_frpc",
                                       "cmd": "stop",
                                       "param": {}}}
                ret2, ret_content = self.TRCloudapi.post_command_to_cloud(gate_datas)
            else:
                ret2, ret_content = True, "网关离线"
            if ret2:
                ret1 = self.remove(self.userinfo.get("local_port_name"))
            if ret1:
                self._vserial_is_running = False
                self.frps_host = None
                self.frps_port = None
                self.frps_token = None
                self.FRPApi = None
                self._stop_time = time.time()
                self.userinfo = {"name": None, "gate": None, "client_online": None, "tunnel_host": None, "token": None,
                         "tunnel_port": None, "tunnel_online": None, "gate_com_params": None, "gate_status": None,
                         "gate_frpc_status": None, "gate_port_name": None, "local_port_name": None, "info": None}
                return True, "停止虚拟串口成功"
            else:
                return False, "关闭失败, 请确认网关是否在线"
        else:
            return False, "服务已停止"

    def vserial_status(self):
        now_time = int(time.time())
        if self._vserial_is_running:
            return True, {"now": now_time,
                          "gate_online": self._gate_online,
                          "gate_frpc_is_running": self._gate_frpc_is_running,
                          "vserial_is_running": self._vserial_is_running,
                           "userinfo": self.userinfo}
        else:
            return False, {"now": now_time, "vserial_is_running": self._vserial_is_running}

    def vserial_ready(self, gate):
        if not self.TRCloudapi:
            self.TRCloudapi = CloudApiv1(self.TRAccesskey)
        gate_online = False
        app_ready = False
        app_info ={}
        gate_status_ret = self.TRCloudapi.get_gate_status(gate)
        if gate_status_ret:
            if gate_status_ret['message'] == "ONLINE":
                gate_online = True
            else:
                gate_online = False
        if gate_online:
            gate_apps_ret = self.TRCloudapi.get_gate_apps(gate)
            if gate_apps_ret:
                gate_apps = gate_apps_ret['message']
                for app in gate_apps:
                    if app.get('info').get('inst') == "freeioe_Vserial_frpc":
                        app_info = app.get('info')
                        break
                for app in gate_apps:
                    if app.get('info').get('inst') == "freeioe_Vserial_frpc" and app.get('info').get(
                            'name') == "APP00000381" and app.get('info').get('running'):
                        app_ready = True
                        break
        return gate_online, {"ready": app_ready, "info": app_info}

    def vserial_action(self, gate, action):
        now_str = str(time.time())
        action_data = {"install": {"id": gate + '/freeioe_Vserial_frpc/install/' + now_str, "device": gate,
                                   "data": {"inst": "freeioe_Vserial_frpc", "name": "APP00000381", "version": 'latest',
                                            "conf": {}}},
                       "start": {"id": gate + '/freeioe_Vserial_frpc/start/' + now_str, "device": gate,
                                 "data": {"inst": "freeioe_Vserial_frpc"}},
                       "stop": {"id": gate + '/freeioe_Vserial_frpc/stop/' + now_str, "device": gate,
                                 "data": {"inst": "freeioe_Vserial_frpc"}},
                       "uninstall": {"id": gate + '/freeioe_Vserial_frpc/uninstall/' + now_str, "device": gate,
                                     "data": {"inst": "freeioe_Vserial_frpc"}},
                       "upgrade": {"id": gate + '/freeioe_Vserial_frpc/upgrade/' + now_str, "device": gate,
                                   "data": {"inst": "freeioe_Vserial_frpc", "name": "APP00000381", "version": 'latest',
                                            "conf": {}}}}
        if not self.TRCloudapi:
            self.TRCloudapi = CloudApiv1(self.TRAccesskey)
        ret, gate_action_ret = None, None
        if action_data.get(action):
            ret, gate_action_ret = self.TRCloudapi.post_action_to_app(action, action_data.get(action))
        return ret, gate_action_ret

    def start(self):
        self._stop_time = time.time()
        threading.Thread.start(self)

    def run(self):
        self._vsport_ctrl = VSPort()
        self._vsport_ctrl.init()
        while not self._thread_stop:
            time.sleep(1)
            if self._vserial_is_running:
                if time.time() - self._start_time > 5:
                    self._start_time = time.time()
                    self.gate_vserial_data()
                    tunnel = self.frpc_tunnel_status()
                    if tunnel:
                        self.userinfo['client_online'] = tunnel.get('status')
                        if tunnel.get('cur_conns'):
                            self.userinfo['tunnel_online'] = True
                        else:
                            self.userinfo['tunnel_online'] = False
                for handler in self._ports:
                    try:
                        gateonline = "OFFLINE"
                        if self._gate_online:
                            gateonline = "ONLINE"
                        vinfo = handler.as_dict()
                        vinfo["now"] = str(int(self._start_time))
                        vinfo["vserial_is_running"] = self._vserial_is_running
                        vinfo["gate_online"] = gateonline
                        vinfo["client_online"] = self.userinfo["client_online"]
                        vinfo["tunnel_online"] = self.userinfo["tunnel_online"]
                        vinfo["gate_frpc_status"] = self.userinfo["gate_frpc_status"]
                        vinfo["gate_port_name"] = self.userinfo["gate_port_name"]
                        vinfo["gate_com_params"] = self.userinfo["gate_com_params"]
                        self._mqtt_stream_pub.vspax_status(handler.get_port_key(), json.dumps(vinfo))
                        self._mqtt_stream_pub.vspax_info(handler.get_port_key(), json.dumps(self.userinfo))
                    except Exception as ex:
                        logging.exception(ex)
                print(self._heartbeat_timeout - time.time())
                if self._enable_heartbeat and time.time() > self._heartbeat_timeout:
                    self._log.warning("heartbeat_timeout is reachable")
                    notice = {"now": str(int(self._start_time)), "notice": "heartbeat_timeout is reachable, stop vserial"}
                    self._mqtt_stream_pub.vspax_notify(handler.get_port_key(), json.dumps(notice))
                    self.stop_vserial()
            else:
                status = {"now": str(int(time.time())), "vserial_is_running": self._vserial_is_running, "local_com": None}
                self._mqtt_stream_pub.vspax_status("COM0", json.dumps(status))

        self._vsport_ctrl.close()
        logging.warning("VSPAX Manager Closed!!!")


    def enable_heartbeat(self, flag, timeout):
        self._enable_heartbeat = flag
        self._heartbeat_timeout = time.time() + timeout
        alive_ret = self.keep_vspax_alive()
        if alive_ret:
            for i in range(4):
                action_ret = self.TRCloudapi.get_action_result(alive_ret.get('message'))
                if action_ret:
                    self.userinfo['gate_status'] = "ONLINE"
                    break
                time.sleep(i + 1)
        return {"enable_heartbeat": self._enable_heartbeat, "heartbeat_timeout": self._heartbeat_timeout}

    def keep_vspax_alive(self):
        if self.userinfo['gate']:
            datas = {
                "id": self.userinfo['gate'] + '/send_output/heartbeat_timeout/' + str(time.time()),
                "device": self.userinfo['gate'],
                "data": {
                    "device": self.userinfo['gate'] + ".freeioe_Vserial_frpc",
                    "output": 'heartbeat_timeout',
                    "value": 60,
                    "prop": "value"
                }
            }
            self.TRCloudapi.action_send_output(datas)

    def frpc_tunnel_status(self):
        proxy = None
        if not self.FRPApi:
            self.FRPApi = frpcManager(self.frps_host)
        proxies = self.FRPApi.frps_tcpTunnels_get('/api/proxy/tcp')
        if proxies:
            for p in proxies.get('proxies'):
                if self.userinfo.get('gate'):
                    if p.get('name') == "vserial_vspax@" + self.userinfo.get('gate'):
                        proxy = p
                        # proxy_status = p.get('status')
                        # proxy_online = p.get('cur_conns')
                        break
        return proxy

    def stop(self):
        self._thread_stop = True
        self.join(3)

    def clean_all(self):
        keys = [h.get_port_key() for h in self._ports]
        for name in keys:
            try:
                self.remove(name)
            except Exception as ex:
                logging.exception(ex)
