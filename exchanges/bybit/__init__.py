"""Адаптер Bybit — WebSocket стримы в Market Data Bus."""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from exchanges import ExchangeBase

logger = logging.getLogger(__name__)

BYBIT_WS_MAIN = "wss://stream.bybit.com/v5/public/linear"
BYBIT_WS_TESTNET = "wss://stream-testnet.bybit.com/v5/public/linear"

CHANNEL_MAP = {
    "candles": "kline",
    "trades": "publicTrade",
    "ticker": "tickers",
    "liquidation": "allLiquidation",
    "orderbook": "orderbook",
}


class BybitExchange(ExchangeBase):
    name = "bybit"

    def __init__(self, testnet: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._ws_url = BYBIT_WS_TESTNET if testnet else BYBIT_WS_MAIN
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ping_task: asyncio.Task | None = None

    async def connect(self):
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._ws_url, heartbeat=20)
        logger.info("Bybit WS connected")

    async def disconnect(self):
        if self._ping_task:
            self._ping_task.cancel()
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("Bybit WS disconnected")

    async def subscribe(self, channel: str, symbols: list[str], params: str | None = None):
        """
        Подписаться на канал.
        params: для kline = timeframe (1, 3, 5, 15, 60, D, W, M — без 'm'), для orderbook = глубина ('200')
        """
        ws_channel = CHANNEL_MAP.get(channel, channel)
        fmt_symbols = [s.replace('/', '').replace(':USDT', '') for s in symbols]

        if ws_channel == "kline":
            tf = params or "1"
            args = [f"kline.{tf}.{s}" for s in fmt_symbols]
        elif ws_channel == "orderbook":
            depth = params or "50"
            args = [f"orderbook.{depth}.{s}" for s in fmt_symbols]
        else:
            args = [f"{ws_channel}.{s}" for s in fmt_symbols]

        msg = {"op": "subscribe", "args": args}
        if self._ws:
            await self._ws.send_json(msg)
            for a in args:
                self._subscriptions.add(a)
            logger.info("Bybit subscribed: %s (%d args)", ws_channel, len(args))

    async def unsubscribe(self, channel: str, symbols: list[str], params: str | None = None):
        ws_channel = CHANNEL_MAP.get(channel, channel)
        fmt_symbols = [s.replace('/', '').replace(':USDT', '') for s in symbols]

        if ws_channel == "kline":
            tf = params or "1"
            args = [f"kline.{tf}.{s}" for s in fmt_symbols]
        elif ws_channel == "orderbook":
            depth = params or "50"
            args = [f"orderbook.{depth}.{s}" for s in fmt_symbols]
        else:
            args = [f"{ws_channel}.{s}" for s in fmt_symbols]

        msg = {"op": "unsubscribe", "args": args}
        if self._ws:
            await self._ws.send_json(msg)

    async def _listen(self):
        if not self._ws:
            return
        msg = await self._ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            await self._handle_message(msg.data)
        elif msg.type == aiohttp.WSMsgType.PING:
            await self._ws.pong()
        elif msg.type == aiohttp.WSMsgType.CLOSED:
            logger.warning("Bybit WS closed — reconnecting")
            await self._reconnect()

    async def _reconnect(self):
        """Переподключиться и восстановить все подписки."""
        old_subs = list(self._subscriptions)
        self._subscriptions = set()
        await self.disconnect()
        await asyncio.sleep(3)
        await self.connect()
        # Восстанавливаем подписки
        for sub in old_subs:
            # sub вида: kline.1.BTCUSDT, publicTrade.BTCUSDT, orderbook.200.100ms.BTCUSDT
            # Отправляем сырой args
            msg = {"op": "subscribe", "args": [sub]}
            if self._ws:
                await self._ws.send_json(msg)
                self._subscriptions.add(sub)
        logger.info("Bybit WS reconnected with %d subscriptions", len(old_subs))

    async def _handle_message(self, raw: str):
        import orjson

        try:
            data = orjson.loads(raw)
        except Exception:
            return

        # Debug: log every message
        logger.debug("WS raw: %s", raw[:200])

        if data.get("op") == "subscribe":
            logger.info("Bybit sub response: success=%s ret_msg=%s", data.get("success"), data.get("ret_msg"))
            return

        if data.get("type") == "snapshot" and "data" not in data:
            return  # subscription confirmation

        topic = data.get("topic", "")
        if not topic:
            return

        # topic examples:
        #   kline.15.BTCUSDT  |  publicTrade.BTCUSDT  |  tickers.BTCUSDT
        #   orderbook.200.100ms.BTCUSDT  |  allLiquidation.BTCUSDT
        channel_parts = topic.split(".")
        ws_channel = channel_parts[0]

        # Извлекаем символ из тела сообщения (data.s), либо из topic (последняя часть)
        inner = data.get("data", {})
        if isinstance(inner, dict):
            raw_symbol = inner.get("s", "") or channel_parts[-1]
        else:
            raw_symbol = channel_parts[-1]

        if not raw_symbol:
            return

        # Bybit отправляет BTCUSDT → конвертируем в BTC/USDT:USDT
        # Ищем позицию USDT в конце
        if raw_symbol.endswith("USDT"):
            base = raw_symbol[:-4]
            symbol = f"{base}/USDT:USDT"
        else:
            symbol = raw_symbol

        ts = data.get("ts", time.time() * 1000)
        bus_channel = self._map_channel(ws_channel)
        # Проброс timeframe из kline.X в канал: candles.1m.BTC/USDT:USDT
        tf_raw = channel_parts[1] if len(channel_parts) >= 3 and ws_channel == "kline" else None
        if tf_raw:
            tf_map = {"1": "1m", "5": "5m", "15": "15m"}
            tf = tf_map.get(tf_raw, f"{tf_raw}m")
            full_channel = f"{bus_channel}.{tf}.{symbol}"
        else:
            full_channel = f"{bus_channel}.{symbol}"

        if "data" in data:
            self._emit(full_channel, symbol, data["data"], ts)

    def _map_channel(self, ws_channel: str) -> str:
        rev = {v: k for k, v in CHANNEL_MAP.items()}
        return rev.get(ws_channel, ws_channel)
