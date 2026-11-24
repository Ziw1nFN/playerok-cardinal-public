from __future__ import annotations

PERMS = ("chats", "deals", "refunds", "stats", "settings")
PERM_TITLES = {
    "chats": "💬 Чаты и ответы",
    "deals": "📋 Сделки",
    "refunds": "↩️ Возвраты",
    "stats": "📊 Статистика",
    "settings": "⚙️ Настройки",
}
ANY = "any"
OWNER = "owner"

_GROUPS = {
    ANY: "menu close accs accsel mn",
    "chats": "reply chl ch1 cht tpl ttp tdl",
    "deals": "dl dl1 chd sent sent_yes tsn tsn_yes tno",
    "refunds": "refund refund_yes trf trf_yes",
    "stats": "st str",
    "settings": "sw num txt txtedit ar ar1 arnew arcmd artxt arntf ardel ardel_yes ad ad1 adnew adpick adname adtxt "
                "adtgl adst adadd adget adclr adclr_yes addel addel_yes dr drt drx drh drw tp tp1 tpnew tptitle tptxt "
                "tpdel tpdel_yes pf pf1 pfn pft pfe pfx pfxk pfc pfca pfcd pfm pfd pfd_yes cs cst csu csu_yes restore rsl",
}
CALLBACK_PERMS = {key: perm for perm, keys in _GROUPS.items() for key in keys.split()}

OWNER_KEYS = set(
    "acc acccheck accdel accdel_yes accproxy accproxy_rm accreset accreset_yes acctgl acctoken accua accadd "
    "accproxy_skip bl bladd blrm pl plg pltgl plcmd pldel pldel_yes prof logs sb ntf nsw raise pfr plugin_settings "
    "sf sf1 sfp sfa sfd sfd_yes sfo sfo_yes sfn sfnp sfna sfnc sfr "
    "wd wdp wdp1 wdc wdb wdpm wdm wde wdn wdn_yes wdv wdt wdu".split()
)
OWNER_KEYS.update({'ctl', 'ctll', 'ctlu', 'ctlb', 'ctlmsg', 'ctlup', 'upd', 'upd_yes'})

COMMAND_PERMS = {"start": ANY, "menu": ANY, "help": ANY, "cancel": ANY}

KIND_PERMS = {
    "newMessage": "chats", "autoResponse": "chats",
    "newDeal": "deals", "delivery": "deals", "dealConfirmed": "deals", "dealProblem": "deals",
    "dealRolledBack": "deals", "review": "deals",
}

RAISE_NUMBERS = ("raiseInterval", "raiseMaxPrice", "raiseDailyLimit", "raiseItems")


def money_sensitive(data: str) -> bool:
    parts = data.split(":")
    key = parts[0]
    if key in ("raise", "pfr"):
        return True
    if key == "sw" and len(parts) > 2 and parts[2] == "autoRaise":
        return True
    if key == "num" and len(parts) > 2 and parts[2] in RAISE_NUMBERS:
        return True
    if key == "pfe" and len(parts) > 3 and parts[3] in ("interval", "maxprice"):
        return True
    return key == "st" and len(parts) > 1 and parts[1] == "all"


def required(data: str) -> str:
    if money_sensitive(data):
        return OWNER
    return CALLBACK_PERMS.get(data.split(":", 1)[0], OWNER)
