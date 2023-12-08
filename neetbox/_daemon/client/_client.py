import functools
import json
import logging
from collections import defaultdict
from threading import Thread
from typing import Callable

import httpx
import websocket

from neetbox._daemon._protocol import *
from neetbox.config import get_module_level_config, get_project_id, get_run_id
from neetbox.logging.formatting import LogStyle
from neetbox.logging.logger import Logger
from neetbox.utils.mvc import Singleton

logger = Logger(whom=None, style=LogStyle(skip_writers=["ws"]))

httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.ERROR)

_ws_initialized = False  # indicate whether websocket has been initialized


def _load_http_client():
    __local_http_client = httpx.Client(
        proxies={
            "http://": None,
            "https://": None,
        }
    )  # type: ignore
    return __local_http_client


def addr_of_api(api, root=None):
    if not root:
        _cfg = get_module_level_config()
        _daemon_server_address = f"{_cfg['host']}:{_cfg['port']}"
        root = f"http://{_daemon_server_address}"
    if not api.startswith("/"):
        api = f"/{api}"
    return f"{root}{api}"


# singleton
class ClientConn(metaclass=Singleton):
    # http client
    http: httpx.Client = _load_http_client()

    def post(api: str, root=None, *args, **kwargs):
        return ClientConn.http.post(addr_of_api(api, root), *args, **kwargs)

    def get(api: str, root=None, *args, **kwargs):
        return ClientConn.http.get(addr_of_api(api, root), *args, **kwargs)

    def put(api: str, root=None, *args, **kwargs):
        return ClientConn.http.put(addr_of_api(api, root), *args, **kwargs)

    def delete(api: str, root=None, *args, **kwargs):
        return ClientConn.http.delete(addr_of_api(api, root), *args, **kwargs)

    __ws_client: websocket.WebSocketApp = None  # _websocket_client
    __ws_subscription = defaultdict(lambda: {})  # default to no subscribers

    def ws_subscribe(event_type_name: str, name: str = None):
        """let a function subscribe to ws messages with event type name.
        !!! dfor inner APIs only, do not use this in your code!
        !!! developers should contorl blocking on their own functions

        Args:
            function (Callable): who is subscribing the event type
            event_type_name (str, optional): Which event to listen. Defaults to None.
        """
        return functools.partial(
            ClientConn._ws_subscribe, event_type_name=event_type_name, name=name
        )

    def _ws_subscribe(function: Callable, event_type_name: str, name=None):
        name = name or function.__name__
        ClientConn.__ws_subscription[event_type_name][name] = function
        logger.debug(f"ws: {name} subscribed to '{event_type_name}")

    def _init_ws():
        global _ws_initialized
        if _ws_initialized:
            return

        cfg = get_module_level_config()
        # ws server url
        ClientConn.ws_server_addr = f"ws://{cfg['host']}:{cfg['port'] + 1}"

        # create websocket app
        logger.log(
            f"creating websocket connection to {ClientConn.ws_server_addr}", skip_writers=["ws"]
        )
        ClientConn.wsApp = websocket.WebSocketApp(
            ClientConn.ws_server_addr,
            on_open=ClientConn.__on_ws_open,
            on_message=ClientConn.__on_ws_message,
            on_error=ClientConn.__on_ws_err,
            on_close=ClientConn.__on_ws_close,
        )

        Thread(
            target=ClientConn.wsApp.run_forever, kwargs={"reconnect": True}, daemon=True
        ).start()  # initialize and start ws thread

        _ws_initialized = True

    def __on_ws_open(ws: websocket.WebSocketApp):
        project_id = get_project_id()
        logger.ok(f"client websocket connected. sending handshake as '{project_id}'...")
        handshake_msg = EventMsg(  # handshake request message
            project_id=project_id,
            run_id=get_run_id(),
            event_type=EVENT_TYPE_NAME_HANDSHAKE,
            who=IdentityType.CLI,
            event_id=0,
        ).dumps()
        ws.send(handshake_msg)

        @ClientConn.ws_subscribe(event_type_name=EVENT_TYPE_NAME_HANDSHAKE)
        def _handle_handshake(msg: EventMsg):
            assert msg.payload["result"] == 200
            logger.ok(f"handshake succeed.")
            ClientConn.__ws_client = ws

    def __on_ws_err(ws: websocket.WebSocketApp, msg):
        logger.err(f"client websocket encountered {msg}")

    def __on_ws_close(ws: websocket.WebSocketApp, close_status_code, close_msg):
        logger.warn(f"client websocket closed")
        if close_status_code or close_msg:
            logger.warn(f"ws close status code: {close_status_code}")
            logger.warn("ws close message: {close_msg}")
        ClientConn.__ws_client = None

    def __on_ws_message(ws: websocket.WebSocketApp, message):
        message = EventMsg.loads(message)  # message should be json
        if message.event_type not in ClientConn.__ws_subscription:
            logger.warn(
                f"Client received a(n) {message.event_type} event but nobody subscribes it. Ignoring anyway."
            )
        for name, subscriber in ClientConn.__ws_subscription[message.event_type].items():
            try:
                subscriber(message)  # pass payload message into subscriber
            except Exception as e:
                # subscriber throws error
                logger.err(
                    f"Subscriber {name} crashed on message event {message.event_type}, ignoring."
                )

    def ws_send(event_type: str, payload: dict, timestamp: str = None, event_id=-1):
        if ClientConn.__ws_client:  # if ws client exist
            try:
                ClientConn.__ws_client.send(
                    EventMsg(
                        project_id=get_project_id(),
                        run_id=get_run_id(),
                        event_type=event_type,
                        event_id=event_id,
                        who=IdentityType.CLI,
                        payload=payload,
                        timestamp=timestamp or get_timestamp(),
                    ).dumps()
                )
            except Exception as e:
                logger.warn(f"websocket send fialed: {e}, message dropped.")


# assign this connection to websocket log writer
from neetbox.logging._writer import _assign_connection_to_WebSocketLogWriter

_assign_connection_to_WebSocketLogWriter(ClientConn)
connection = ClientConn
