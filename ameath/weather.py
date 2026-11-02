"""City weather with dated facts and a bounded, persona-aware greeting."""
from __future__ import annotations

import json
import math
import re
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from .config import Config
from .llm import ModelClient, ModelError, NoRedirect
from .memory import Persona, PersonaCard


class WeatherError(Exception):
    pass


def read_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Ameath/0.6", "Accept": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            raw = response.read(256001)
        if len(raw) > 256000:
            raise ValueError("oversized response")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("error"):
            raise ValueError("invalid response")
        return result
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise WeatherError("天气服务暂时连接不上，稍后可以再试。") from None


def search_cities(query):
    if not 2 <= len(query.strip()) <= 80:
        raise WeatherError("请输入至少两个字的城市名称。")
    data = read_json("https://geocoding-api.open-meteo.com/v1/search?" + urlencode(
        {"name": query.strip(), "count": 8, "language": "zh", "format": "json"}))
    cities = []
    for item in data.get("results", [])[:8]:
        try:
            latitude, longitude = float(item["latitude"]), float(item["longitude"])
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                continue
            label = " · ".join(str(item.get(key, "")) for key in ("name", "admin1", "country") if item.get(key))
            cities.append({"city": label[:120], "latitude": latitude, "longitude": longitude,
                           "timezone": str(item.get("timezone") or "auto")[:80]})
        except (KeyError, TypeError, ValueError):
            continue
    return cities


CODES = {0: "晴天", 1: "大部晴朗", 2: "多云", 3: "阴天", 45: "有雾", 48: "有冻雾",
         51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨", 56: "冻雨", 57: "冻雨",
         61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
         71: "小雪", 73: "中雪", 75: "大雪", 77: "有雪粒", 80: "阵雨", 81: "阵雨", 82: "强阵雨",
         85: "阵雪", 86: "强阵雪", 95: "雷雨", 96: "雷雨伴冰雹", 97: "强雷雨", 99: "雷雨伴冰雹"}


@dataclass(frozen=True)
class WeatherReport:
    city: str
    day: str
    low: float
    high: float
    code: int
    mean: float | None = None
    yesterday_mean: float | None = None
    probability: float | None = None
    fetched_at: str = ""

    @property
    def condition(self):
        return CODES.get(self.code, "天气有些变化")

    @property
    def facts(self):
        city = self.city.split(" · ")[0]
        text = f"我看了{city}今天的预报，预计{self.condition}，气温 {self.low:g}～{self.high:g}℃。"
        if self.mean is not None and self.yesterday_mean is not None:
            difference = round(self.mean - self.yesterday_mean, 1)
            if abs(difference) < 1:
                text += "整体和昨天差不多呢。"
            else:
                text += f"整体比昨天{'凉' if difference < 0 else '暖'}了约 {abs(difference):g}℃呢。"
        return text

    @property
    def fallback_ending(self):
        if self.code >= 95:
            return "出门记得留意雷雨呀。等天气好些，我们再一起去看看学园超市的新东西，好不好？"
        if "雪" in self.condition or "冻" in self.condition:
            return "外套和围巾可别落下。要是能把暖乎乎的饮料递给你就好了——那先让我陪着你吧。"
        if "雨" in self.condition or (self.probability or 0) >= 50:
            return "出门可别忘了伞呀。欸～下次要不要一起去学园超市挑把新伞？我帮你看花色！"
        if self.high >= 30:
            return "水杯要放在手边哦。忙完陪我歇一小会儿吧，我还有好多话想和你说。"
        return "出门记得按温度添减衣服呀。等你忙完，要不要陪我去学园超市逛逛？就一小会儿～"

    @property
    def source_note(self):
        return f"Open-Meteo · {self.city} · {self.day}\n获取于 {self.fetched_at}\n温差按日均气温比较；昨天的数据为天气模型估计。\nhttps://open-meteo.com/"


def parse_report(data, city, today):
    try:
        daily = data["daily"]
        index = daily["time"].index(today.isoformat())
        def number(key, i=index, *, required=False):
            values = daily.get(key, [])
            value = values[i] if 0 <= i < len(values) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                if required:
                    raise ValueError("missing weather data")
                return None
            return round(float(value), 1)
        low = number("temperature_2m_min", required=True)
        high = number("temperature_2m_max", required=True)
        code = int(number("weather_code", required=True))
        if low > high or not -100 <= low <= high <= 65 or code not in CODES:
            raise ValueError("invalid weather data")
        yesterday = (today - timedelta(days=1)).isoformat()
        previous = daily["time"].index(yesterday) if yesterday in daily["time"] else -1
        return WeatherReport(city, today.isoformat(), low, high, code, number("temperature_2m_mean"),
                             number("temperature_2m_mean", previous), number("precipitation_probability_max"),
                             datetime.now().astimezone().isoformat(timespec="minutes"))
    except (KeyError, TypeError, ValueError, IndexError):
        raise WeatherError("没有取得今天的完整天气数据，先不猜天气啦。") from None


def greeting_period(now):
    return "早上好" if 5 <= now.hour < 11 else "中午好" if 11 <= now.hour < 14 else "下午好" if 14 <= now.hour < 18 else "晚上好"


class WeatherService:
    def __init__(self, config: Config):
        self.config = config
        self.client = ModelClient(config, "companion")

    def fetch(self, *, today=None, settings=None):
        settings = settings or self.config.settings
        if not settings.weather_city or settings.weather_latitude is None or settings.weather_longitude is None:
            raise WeatherError("先在「天气与问候」里选择城市，我才能看看你那边的天气呀。")
        params = {"latitude": settings.weather_latitude, "longitude": settings.weather_longitude,
                  "daily": "weather_code,temperature_2m_max,temperature_2m_min,temperature_2m_mean,precipitation_probability_max",
                  "timezone": settings.weather_timezone, "past_days": 1, "forecast_days": 1, "temperature_unit": "celsius"}
        data = read_json("https://api.open-meteo.com/v1/forecast?" + urlencode(params))
        return parse_report(data, settings.weather_city, today or datetime.now().astimezone().date())

    def greet(self, report, cancelled, *, now=None, settings=None):
        settings = replace(settings or self.config.settings)
        now = now or datetime.now().astimezone()
        opening = greeting_period(now) + "呀，我的大英雄～"
        ending = report.fallback_ending
        # Only the conversational edges are generated. Weather and temperature comparisons stay factual.
        if settings.mode == "live" and not cancelled.is_set():
            persona = PersonaCard(self.config.directory).load() if settings.use_companion_context else Persona()
            system = ("你是用户桌面上的伙伴，按照下方人设写一段每日见面的开场和收尾。"
                      "用户是与你亲近的漂泊者。中间的天气事实由应用原样插入，你只写两侧的自然对话。"
                      "只返回 JSON：{\"opening\":\"开场问候\",\"ending\":\"收尾闲聊\"}。"
                      "开场必须以给定的时段问候开头，少于35字；收尾20至90字。"
                      "不要重复天气状况、气温、日期或温差，不写任何数字；不要预测新的天气或编造用户的经历。"
                      "可邀请对方日后一起逛学园超市、休息、喝热饮；采用邀请或想象语气，不声称真的出门购物。"
                      "避免客服口吻和清单。人设：" + json.dumps(persona.to_dict(), ensure_ascii=False))
            content = json.dumps({"时段问候": greeting_period(now), "中间原文": report.facts,
                                  "收尾参考": ending}, ensure_ascii=False)
            payload = ({"model": settings.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                        "max_completion_tokens": 650, "stream": False} if settings.protocol == "chat" else
                       {"model": settings.model, "instructions": system, "input": content, "max_output_tokens": 650, "store": False})
            try:
                raw = self.client.request(payload, cancelled, thinking=False, settings=settings, detail="weather").text.strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                data = json.loads(raw)
                candidate_open, candidate_end = data.get("opening"), data.get("ending")
                if (isinstance(candidate_open, str) and isinstance(candidate_end, str)
                        and candidate_open.startswith(greeting_period(now)) and 3 <= len(candidate_open) <= 35
                        and 10 <= len(candidate_end) <= 100 and not re.search(r"[\d℃°]|气温|温度|降温|升温|比昨天", candidate_open + candidate_end)):
                    opening, ending = candidate_open, candidate_end
            except (ModelError, ValueError, AttributeError, TypeError):
                pass
        return opening + "\n\n" + report.facts + "\n\n" + ending


class DailyGreetingStore:
    def __init__(self, directory):
        self.path = directory / "daily-greeting.json"

    def already_shown(self, day):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data.get("last_shown") == day.isoformat()
        except (OSError, ValueError, AttributeError):
            return False

    def mark_shown(self, day):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"last_shown": day.isoformat()}), encoding="utf-8")
        temporary.replace(self.path)
