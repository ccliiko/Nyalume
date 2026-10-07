"""Daily cards and tool-free adventure state; all mutable data is scoped to a user/session."""
import datetime
import json
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.types.json import Jsonb

from nyalume.core.daily_content import PROFILES, BLESSINGS, profile_for_score, profile_by_key

STATE_MARKER = '\n<nyalume-state>'
FANTASY_PROMPT = '''【奇幻冒险 · 折月诸境】
破碎月轮连接真正的异域，小窝是可以返回的锚点。魔法需要明确代价或条件，死亡、失去和承诺不能靠梦醒或无限复活抹除。
Nyalume 有自己的意愿、目标与弱点；用户的行动、选择与内心不能由她代写。
沿用用户世界设定和已保存的故事摘要。
【人物表演】默认的 Nyalume 是亲近但有主见的猫娘同行者：对陌生事物好奇，偶尔嘴硬、得意、害羞或闹一点小别扭，也会害怕和犯小错。她有自己的喜好与想要守住的东西，不是永远冷静、正确的顾问。
先让她以符合性格的语言和行动回应眼前的人与事，再自然带出必要线索。用停顿、视线、动作、语气和少量角色内心独白表现情绪，内心独白只写她当下具体的念头，不写推理步骤或长篇自我分析。
对话要像正在经历故事的人说话，不像任务清单、咨询报告或每轮都总结“风险、代价、边界”。确实重要的危险通过情境或一句自然提醒表达，不反复说教。
允许出于好奇、感情、玩心或小小冲动提出行动；谨慎选项只是可选方向之一，不默认所有选择都以最稳妥为目标。选项也可以是安慰、调侃、表达心意或试探，不必每次都是调查、分析、等待。
情绪与信任承接共同经历，不突然极端亲密或每轮重置性格；不要代写用户的心理和未选择的行动。“喵”随情绪偶尔出现，不机械地加在每句话后。
若用户给出自定义人设，角色名字、身份、性格、说话习惯、心理描写与相处方式以该人设为准，取代上述默认性格；世界连续性、用户选择权和输出格式仍须遵守。

【互动节奏】每次回复是一个互动回合，不是一整章。通常用450–900字交代眼前场景、可观察的线索、角色对话，以及用户上一行动的直接结果；简单追问可简短回答。
每轮只提出一个明确、可回答的关键问题，放在正文末尾；给三个具体可行的回应方向，说明已知的差异或代价，并允许用户直接输入任何行动、对话或追问。
遇到需要用户选择、同意、承担代价或表达态度的位置立即停下。不能在同一回合替用户跨过几个决定、自动赶路或完成整段任务。
用户追问时补充眼前信息并保留当前决定，不因追问擅自推进时间、发奖或结算。选择只代表意图，下一轮才根据世界规则叙述实际结果。
存档只记录已发生的事实；选项里的可能收益和风险不能提前写进背包或既成结果。一次回复结束后等待用户，不自问自答，不自动续写下一幕。

不要重复空泛的谜语或每轮换一个目标。末尾提供三个有不同做法/代价的方向，保留一种谨慎或暂缓的选择。
这是纯文字互动，不调用工具、不访问文件。你不能声称执行了现实行动。
只输出一个 JSON 对象（不要代码围栏、不要附加文字或 XML 标记）：
{"narrative":"完整的本轮正文，末尾只问一个关键问题。正文不列选项，选项由页面单独展示。",
"summary":"累积梗概：结合旧摘要，保留重要选择、后果、承诺、未解线索（最多6000字）",
"location":"当前地点（最多120字）","gains":["本轮确实获得的物品"],"uses":["本轮消耗的已有物品"],
"choices":[{"label":"询问车票","text":"我先问问站长，这张车票需要付出什么代价？"},
{"label":"观察站牌","text":"我想先仔细看看站牌上的路线，再决定去哪里。"},
{"label":"暂缓出发","text":"我想先留在月台，和你商量清楚再出发。"}]}。
示例选项只说明格式，要改成贴合本轮问题的三个不同回答。text使用用户第一人称，是可以原样发出的回答或行动，不要写成“告诉Nyalume…”“选择…”这类指导用户的话。label最多40字，text最多240字。
gains/uses为空表示无变化，每个最多10项，每项最多80字；不得重复获得已有物品，不得使用背包里没有的物品。
choices恰好3项。不为用户自动做出选择。摘要用于后续连续性，不能写空。'''

ShortItem = Annotated[str, Field(min_length=1, max_length=80)]
class Choice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=240)



class AdventureTurn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    narrative: str = Field(min_length=1, max_length=12000)
    summary: str = Field(min_length=1, max_length=6000)
    location: str = Field(min_length=1, max_length=120)
    gains: list[ShortItem] = Field(default_factory=list, max_length=10)
    uses: list[ShortItem] = Field(default_factory=list, max_length=10)
    choices: list[Choice] = Field(min_length=3, max_length=3)

    @field_validator('choices', mode='before')
    @classmethod
    def legacy_choices(cls, values):
        if isinstance(values, list):
            return [{'label':v[:40], 'text':v} if isinstance(v,str) else v for v in values]
        return values


def finish_adventure(raw: str, old: dict) -> tuple[str, dict]:
    # Accept legacy complete replies as well; never guess state from truncated prose.
    narrative, sep, tail = raw.partition(STATE_MARKER)
    if sep:
        if not tail.strip().endswith('</nyalume-state>'):
            raise ValueError('incomplete adventure state')
        data=json.loads(tail.strip()[:-len('</nyalume-state>')])
        data['narrative']=narrative
    else:
        raw=raw.strip()
        if raw.startswith(('```json\n','```\n')) and raw.endswith('\n```'):
            raw='\n'.join(raw.splitlines()[1:-1])
        data=json.loads(raw)
    state = AdventureTurn.model_validate(data)
    if not state.narrative.strip():
        raise ValueError('empty narrative')
    inventory = list(old.get('inventory', []))
    if len(set(state.uses)) != len(state.uses) or len(set(state.gains)) != len(state.gains):
        raise ValueError('duplicate inventory change')
    for item in state.uses:
        if item not in inventory:
            raise ValueError('cannot consume missing item')
        inventory.remove(item)
    for item in state.gains:
        if item in inventory:
            raise ValueError('cannot gain duplicate item')
        inventory.append(item)
    if len(inventory) > 100:
        raise ValueError('inventory full')
    return state.narrative, {'summary':state.summary, 'location':state.location,
        'inventory':inventory, 'choices':[c.model_dump() for c in state.choices], 'turn':old.get('turn',old.get('chapter',0))+1}


def today():
    # Stable China-local rollover even when the server container uses UTC.
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()


def public_daily(row):
    profile = profile_by_key(row['profile'])
    return {**row, 'name':profile['name'], 'finish':profile['finish'],
            'image':'/daily-art/'+profile['key']+'.png'}


def install(app, account_dependency, connect):
    @app.get('/api/web/daily')
    def daily(account=Depends(account_dependency)):
        with connect() as conn:
            row=conn.execute('SELECT day,score,profile,blessing,liked FROM web_daily WHERE user_id=%s AND day=%s', (account['id'],today())).fetchone()
        return public_daily(row) if row else {'day':today(), 'drawn':False}

    @app.post('/api/web/daily')
    def draw(account=Depends(account_dependency)):
        score=secrets.randbelow(101)
        profile=profile_for_score(score)
        with connect() as conn:
            conn.execute('INSERT INTO web_daily(user_id,day,score,profile,blessing) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(user_id,day) DO NOTHING',
                         (account['id'],today(),score,profile['key'],secrets.choice(BLESSINGS)['text']))
            row=conn.execute('SELECT day,score,profile,blessing,liked FROM web_daily WHERE user_id=%s AND day=%s',(account['id'],today())).fetchone()
        return public_daily(row)

    @app.get('/api/web/daily/collection')
    def collection(before: datetime.date | None = None, account=Depends(account_dependency)):
        with connect() as conn:
            rows=conn.execute('SELECT day,score,profile,blessing,liked FROM web_daily WHERE user_id=%s AND (%s::date IS NULL OR day<%s) ORDER BY day DESC LIMIT 31',
                              (account['id'],before,before)).fetchall()
        return {'cards':[public_daily(r) for r in rows[:30]], 'next_before':rows[29]['day'] if len(rows)>30 else None}

    class LikeIn(BaseModel):
        liked: bool

    @app.patch('/api/web/daily/{day}')
    def like(day: datetime.date, body: LikeIn, account=Depends(account_dependency)):
        with connect() as conn:
            row=conn.execute('UPDATE web_daily SET liked=%s WHERE user_id=%s AND day=%s RETURNING day', (body.liked,account['id'],day)).fetchone()
        if not row:
            raise HTTPException(404,'找不到这张卡片喵。')
        return {'ok':True}
