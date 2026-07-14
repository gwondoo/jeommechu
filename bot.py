from __future__ import annotations

import logging
import os
import random

import discord
from discord import app_commands
from dotenv import load_dotenv

from kakao import KakaoError, KakaoLocalClient
from storage import JsonStore, now_iso


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger("jeommechu")

KIND_CHOICES = [app_commands.Choice(name=name, value=name) for name in ("한식", "중식", "일식", "양식", "아시아음식", "분식", "치킨", "피자", "카페")]


def guild_id_of(interaction: discord.Interaction) -> int | None:
    return interaction.guild_id


def filter_places(places: list[dict], kind: str | None) -> list[dict]:
    if not kind:
        return places
    token = kind.casefold()
    return [place for place in places if token in f"{place.get('category', '')} {place.get('name', '')}".casefold()]


def map_link(place: dict) -> str | None:
    return place.get("kakao_place_url")


def kakao_map_view(place: dict) -> discord.ui.View | None:
    url = map_link(place)
    if not url:
        return None
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="카카오맵에서 보기", url=url))
    return view


def recommendation_embed(place: dict, source: str, candidate_count: int) -> discord.Embed:
    embed = discord.Embed(title=f"🍚 오늘의 점심: {place['name']}", color=0xF9A825)
    embed.description = place.get("category") or "음식점"
    distance = place.get("distance_meters")
    if distance is not None:
        embed.add_field(name="거리", value=f"회사에서 약 {distance:,}m", inline=True)
    if place.get("phone"):
        embed.add_field(name="전화번호", value=place["phone"], inline=True)
    embed.add_field(name="주소", value=place.get("address") or "주소 정보 없음", inline=False)
    if place.get("memo"):
        embed.add_field(name="메모", value=place["memo"], inline=False)
    embed.set_footer(text=f"{source} · 후보 {candidate_count}곳 중에서 골랐어요.")
    return embed


class DeleteConfirmView(discord.ui.View):
    def __init__(self, store: JsonStore, guild_id: int, restaurant_name: str, requester_id: int) -> None:
        super().__init__(timeout=60)
        self.store = store
        self.guild_id = guild_id
        self.restaurant_name = restaurant_name
        self.requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("삭제 요청을 실행한 사람만 선택할 수 있습니다.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await self.store.delete_restaurant(self.guild_id, self.restaurant_name)
        except KeyError:
            await interaction.response.edit_message(content="이미 삭제되었거나 찾을 수 없는 식당입니다.", view=None)
            return
        await interaction.response.edit_message(content=f"✅ ‘{self.restaurant_name}’을 식권대장에서 삭제했습니다.", view=None)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="삭제를 취소했습니다.", view=None)


class LunchBot(discord.Client):
    def __init__(self, store: JsonStore, kakao: KakaoLocalClient, dev_guild_id: int | None) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.store = store
        self.kakao = kakao
        self.dev_guild = discord.Object(id=dev_guild_id) if dev_guild_id else None

    async def setup_hook(self) -> None:
        await self.store.load()
        if self.dev_guild:
            self.tree.copy_global_to(guild=self.dev_guild)
            await self.tree.sync(guild=self.dev_guild)
            # Development guild commands appear immediately. Remove the previous
            # global registration so Discord does not show every command twice.
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            LOGGER.info("Slash commands synced to development guild %s", self.dev_guild.id)
        else:
            await self.tree.sync()
            LOGGER.info("Global slash commands synced")


load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
KAKAO_KEY = os.getenv("KAKAO_REST_API_KEY")
DEV_GUILD = int(os.environ["DISCORD_GUILD_ID"]) if os.getenv("DISCORD_GUILD_ID") else None

if not TOKEN or not KAKAO_KEY:
    raise RuntimeError(".env에 DISCORD_BOT_TOKEN과 KAKAO_REST_API_KEY를 설정해주세요.")

store = JsonStore()
bot = LunchBot(store, KakaoLocalClient(KAKAO_KEY), DEV_GUILD)


async def require_guild(interaction: discord.Interaction) -> int | None:
    guild_id = guild_id_of(interaction)
    if guild_id is None:
        await interaction.response.send_message("이 명령어는 Discord 서버 채널에서만 사용할 수 있습니다.", ephemeral=True)
    return guild_id


@bot.tree.command(name="점메추", description="식권대장 식당 중에서 점심을 추천합니다.")
@app_commands.describe(kind="원하는 음식 종류")
@app_commands.choices(kind=KIND_CHOICES)
async def lunch_recommend(interaction: discord.Interaction, kind: app_commands.Choice[str] | None = None) -> None:
    guild_id = await require_guild(interaction)
    if guild_id is None:
        return
    places = filter_places(await store.restaurants(guild_id), kind.value if kind else None)
    if not places:
        message = "식권대장에 등록된 식당이 없습니다. `/식권대장 추가`로 먼저 등록해주세요."
        if kind:
            message = f"식권대장에 ‘{kind.value}’ 조건에 맞는 식당이 없습니다."
        await interaction.response.send_message(message)
        return
    place = random.choice(places)
    await interaction.response.send_message(
        embed=recommendation_embed(place, "식권대장", len(places)),
        view=kakao_map_view(place),
    )


@bot.tree.command(name="식권대장싫어", description="회사 주변 모든 식당 중에서 점심을 추천합니다.")
@app_commands.describe(kind="원하는 음식 종류", distance="검색 반경(미터). 기본값은 회사 설정값입니다.")
@app_commands.choices(kind=KIND_CHOICES)
async def nearby_recommend(
    interaction: discord.Interaction,
    kind: app_commands.Choice[str] | None = None,
    distance: app_commands.Range[int, 100, 20000] | None = None,
) -> None:
    guild_id = await require_guild(interaction)
    if guild_id is None:
        return
    guild = await store.get_guild(guild_id)
    company = guild.get("company")
    if not company:
        await interaction.response.send_message("먼저 `/회사주소설정`으로 회사 주소를 설정해주세요.")
        return
    radius = int(distance or company.get("default_radius", 700))
    await interaction.response.defer(thinking=True)
    try:
        places = await bot.kakao.nearby_restaurants(company["longitude"], company["latitude"], radius, kind.value if kind else None)
    except KakaoError:
        LOGGER.exception("Kakao nearby search failed")
        await interaction.followup.send("주변 음식점 정보를 가져오지 못했습니다. 잠시 후 다시 시도해주세요.")
        return
    if not places:
        await interaction.followup.send("조건에 맞는 주변 음식점을 찾지 못했습니다. 거리나 종류를 바꿔보세요.")
        return
    await store.set_nearby_cache(guild_id, radius, places)
    place = random.choice(places)
    registered = any(
        (r.get("kakao_place_id") and r.get("kakao_place_id") == place.get("kakao_place_id"))
        or (r["name"].casefold() == place["name"].casefold() and r.get("address") == place.get("address"))
        for r in guild["meal_ticket_restaurants"]
    )
    embed = recommendation_embed(place, "회사 주변", len(places))
    if registered:
        embed.add_field(name="🎉 참고", value="이 식당은 식권대장에도 등록되어 있어요!", inline=False)
    await interaction.followup.send(embed=embed, view=kakao_map_view(place))


ticket_group = app_commands.Group(name="식권대장", description="식권대장 식당을 관리합니다.")
bot.tree.add_command(ticket_group)


@ticket_group.command(name="목록", description="식권대장 식당 목록을 봅니다.")
@app_commands.describe(kind="음식 종류로 필터링")
@app_commands.choices(kind=KIND_CHOICES)
async def ticket_list(interaction: discord.Interaction, kind: app_commands.Choice[str] | None = None) -> None:
    guild_id = await require_guild(interaction)
    if guild_id is None:
        return
    places = filter_places(await store.restaurants(guild_id), kind.value if kind else None)
    if not places:
        await interaction.response.send_message("등록된 식권대장 식당이 없습니다.")
        return
    lines = []
    for index, place in enumerate(places[:20], start=1):
        distance = f" · {place['distance_meters']:,}m" if place.get("distance_meters") is not None else ""
        lines.append(f"{index}. **{place['name']}** · {place.get('category', '종류 미정')}{distance}")
    embed = discord.Embed(title="🎫 식권대장 목록", description="\n".join(lines), color=0x1976D2)
    embed.set_footer(text=f"총 {len(places)}곳" + (" · 처음 20곳만 표시" if len(places) > 20 else ""))
    await interaction.response.send_message(embed=embed)


@ticket_group.command(name="추가", description="식권대장에 식당을 추가합니다.")
@app_commands.describe(name="식당 이름", kind="음식 종류", address="식당 주소", phone="전화번호", memo="메모")
@app_commands.choices(kind=KIND_CHOICES)
async def ticket_add(
    interaction: discord.Interaction,
    name: str,
    kind: app_commands.Choice[str] | None = None,
    address: str | None = None,
    phone: str | None = None,
    memo: str | None = None,
) -> None:
    guild_id = await require_guild(interaction)
    if guild_id is None:
        return
    guild = await store.get_guild(guild_id)
    company = guild.get("company")
    place: dict = {"name": name, "category": kind.value if kind else "종류 미정", "address": address or "", "phone": phone or "", "latitude": None, "longitude": None, "distance_meters": None, "kakao_place_id": None, "kakao_place_url": None, "memo": memo or ""}
    if company:
        try:
            found = await bot.kakao.find_place(name, company["longitude"], company["latitude"], company.get("default_radius", 700))
            if found:
                place = {**found, **{key: value for key, value in place.items() if value not in (None, "", "종류 미정")}}
        except KakaoError:
            LOGGER.warning("Could not enrich restaurant %s from Kakao", name)
    try:
        added = await store.add_restaurant(guild_id, place)
    except ValueError as exc:
        await interaction.response.send_message(str(exc))
        return
    await interaction.response.send_message(embed=recommendation_embed(added, "식권대장에 추가됨", 1))


@ticket_group.command(name="수정", description="식권대장 식당 정보를 수정합니다.")
@app_commands.describe(restaurant="수정할 식당 이름", name="새 식당 이름", kind="새 음식 종류", address="새 주소", phone="새 전화번호", memo="새 메모")
@app_commands.choices(kind=KIND_CHOICES)
async def ticket_update(
    interaction: discord.Interaction,
    restaurant: str,
    name: str | None = None,
    kind: app_commands.Choice[str] | None = None,
    address: str | None = None,
    phone: str | None = None,
    memo: str | None = None,
) -> None:
    guild_id = await require_guild(interaction)
    if guild_id is None:
        return
    changes = {"name": name, "category": kind.value if kind else None, "address": address, "phone": phone, "memo": memo}
    if all(value is None for value in changes.values()):
        await interaction.response.send_message("바꿀 항목을 하나 이상 입력해주세요.")
        return
    try:
        updated = await store.update_restaurant(guild_id, restaurant, changes)
    except KeyError:
        await interaction.response.send_message(f"‘{restaurant}’을 식권대장에서 찾지 못했습니다.")
        return
    await interaction.response.send_message(embed=recommendation_embed(updated, "식권대장 수정 완료", 1))


@ticket_group.command(name="삭제", description="식권대장에서 식당을 삭제합니다.")
@app_commands.describe(restaurant="삭제할 식당 이름")
async def ticket_delete(interaction: discord.Interaction, restaurant: str) -> None:
    guild_id = await require_guild(interaction)
    if guild_id is None:
        return
    places = await store.restaurants(guild_id)
    if not any(place["name"].casefold() == restaurant.casefold() for place in places):
        await interaction.response.send_message(f"‘{restaurant}’을 식권대장에서 찾지 못했습니다.")
        return
    await interaction.response.send_message(
        f"⚠️ ‘{restaurant}’을 식권대장 목록에서 삭제할까요?",
        view=DeleteConfirmView(store, guild_id, restaurant, interaction.user.id),
    )


@bot.tree.command(name="회사주소설정", description="회사 주소와 주변 검색 반경을 설정합니다.")
@app_commands.describe(address="회사 도로명 또는 지번 주소", radius="기본 검색 반경(미터)")
async def set_company_address(interaction: discord.Interaction, address: str, radius: app_commands.Range[int, 100, 20000] = 700) -> None:
    guild_id = await require_guild(interaction)
    if guild_id is None:
        return
    await interaction.response.defer(thinking=True)
    try:
        coordinate = await bot.kakao.address_to_coordinate(address)
    except KakaoError:
        LOGGER.exception("Kakao geocoding failed")
        await interaction.followup.send("주소를 확인하는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
        return
    if not coordinate:
        await interaction.followup.send("입력한 주소를 찾지 못했습니다. 도로명 주소를 포함해 다시 입력해주세요.")
        return
    company = {**coordinate, "default_radius": int(radius), "updated_at": now_iso()}
    await store.set_company(guild_id, company)
    await interaction.followup.send(f"✅ 회사 주소를 설정했습니다.\n\n📍 {company['address']}\n🔎 기본 검색 반경: {radius:,}m")


@bot.tree.command(name="회사주소조회", description="현재 설정된 회사 주소를 확인합니다.")
async def get_company_address(interaction: discord.Interaction) -> None:
    guild_id = await require_guild(interaction)
    if guild_id is None:
        return
    guild = await store.get_guild(guild_id)
    company = guild.get("company")
    if not company:
        await interaction.response.send_message("아직 회사 주소가 설정되지 않았습니다. `/회사주소설정`을 사용해주세요.")
        return
    await interaction.response.send_message(
        f"🏢 현재 회사 주소\n\n📍 {company['address']}\n🔎 기본 검색 반경: {company['default_radius']:,}m\n🎫 식권대장 등록 식당: {len(guild['meal_ticket_restaurants'])}곳",
    )


@bot.tree.command(name="도움말", description="점메추 봇 명령어를 안내합니다.")
async def help_command(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="🍽️ 점메추 봇 도움말", color=0xF9A825)
    embed.add_field(name="점심 추천", value="`/점메추 [종류]` — 식권대장 식당에서 추천\n`/식권대장싫어 [종류] [거리]` — 회사 주변 전체 식당에서 추천", inline=False)
    embed.add_field(name="식권대장", value="`/식권대장 목록`\n`/식권대장 추가`\n`/식권대장 수정`\n`/식권대장 삭제`", inline=False)
    embed.add_field(name="회사 주소", value="`/회사주소설정 주소 [반경]`\n`/회사주소조회`", inline=False)
    embed.set_footer(text="종류와 거리 같은 옵션은 Discord 입력창에서 선택할 수 있어요.")
    await interaction.response.send_message(embed=embed)


@bot.event
async def on_ready() -> None:
    LOGGER.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")


if __name__ == "__main__":
    bot.run(TOKEN, log_handler=None)
