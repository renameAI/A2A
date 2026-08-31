"""콜드메일 법적 고지 — 보내는 순간 지켜야 하는 것.

이 레포는 초안만 만들다가 실제 발송으로 넘어왔다. 그 선을 넘는 순간
메일은 글이 아니라 **규제 대상**이 된다. 미국 CAN-SPAM은 발신자의 실제
우편 주소와 수신 거부 수단을 본문에 요구하고, EU·영국 GDPR은 정당한
이익에 근거해 보낸다는 고지와 즉시 거부 경로를 요구한다. 일본
특정전자메일법, 태국 PDPA도 같은 계열이다.

참고 자료(실제로 운영 중인 아웃리치 프롬프트 5종 중 4종)가 이것을
**고정 문구**로 박아 두고 있었다 — 모델이 매번 새로 쓸 문장이 아니라는
뜻이다. 그래서 여기서도 코드가 만든다: 모델은 설득을 쓰고, 고지는
코드가 붙인다. 판정과 결정의 분리가 여기서는 법적 안전장치가 된다.

없는 정보를 지어내지 않는다. 주소가 없으면 고지를 만들 수 없고, 고지를
만들 수 없으면 **보내지 않는다** — 빈칸을 그럴듯한 문장으로 채우는 것이
가장 나쁜 실패다.
"""

# 수신 국가 → 준거 법령 이름. 이름을 밝히는 이유는 형식이 아니라 신뢰다:
# 어느 법에 근거해 보내는지 밝힌 메일과 그렇지 않은 메일은 상대에게
# 다르게 읽힌다. 모르는 나라는 GDPR 문구를 쓰지 않는다(적용되지도 않는
# 법을 대며 정당성을 주장하는 것은 그 자체로 거짓이다).
_LAW = {
    "US": "CAN-SPAM Act", "CA": "CASL",
    "GB": "UK GDPR", "UK": "UK GDPR",
    "DE": "GDPR", "FR": "GDPR", "IT": "GDPR", "ES": "GDPR", "NL": "GDPR",
    "BE": "GDPR", "AT": "GDPR", "SE": "GDPR", "DK": "GDPR", "FI": "GDPR",
    "PL": "GDPR", "IE": "GDPR", "PT": "GDPR", "CZ": "GDPR", "GR": "GDPR",
    "JP": "特定電子メール法", "TH": "PDPA", "ID": "UU PDP",
    "KR": "정보통신망법",
}

_NOTICE = {
    "ko": ("본 메일은 공개된 비즈니스 연락처를 근거로 발송된 B2B 제안입니다."
           " 수신을 원하지 않으시면 '수신 거부'라고 회신해 주세요 —"
           " 즉시 삭제하겠습니다."),
    "en": ("This is a B2B message sent to a publicly listed business contact."
           " If you would rather not hear from us, reply with"
           " 'unsubscribe' and we will remove you immediately."),
    "de": ("Dies ist eine B2B-Nachricht an einen öffentlich zugänglichen"
           " Geschäftskontakt. Wenn Sie keine weiteren Nachrichten wünschen,"
           " antworten Sie bitte mit 'Abmelden' — wir löschen Ihre Daten"
           " umgehend."),
    "fr": ("Ce message B2B vous est adressé car vos coordonnées"
           " professionnelles sont publiquement accessibles. Pour ne plus"
           " être contacté, répondez 'Désinscription' — vos données seront"
           " immédiatement supprimées."),
    "ja": ("本メールは公開されている法人連絡先に基づくB2Bのご提案です。"
           "配信停止をご希望の場合は「配信停止」とご返信ください。"
           "直ちに削除いたします。"),
    "es": ("Este es un mensaje B2B enviado a un contacto profesional"
           " públicamente disponible. Si no desea recibir más mensajes,"
           " responda 'baja' y le eliminaremos de inmediato."),
    "it": ("Questo è un messaggio B2B inviato a un contatto professionale"
           " pubblicamente disponibile. Per non ricevere altri messaggi,"
           " risponda 'cancellami' e la rimuoveremo immediatamente."),
    "id": ("Ini adalah pesan B2B yang dikirim ke kontak bisnis yang tersedia"
           " untuk umum. Jika Anda tidak ingin menerima pesan lagi, balas"
           " 'berhenti' dan kami akan menghapus data Anda segera."),
}

# 고지에 반드시 들어가야 하는 발신자 정보. 우편 주소가 핵심이다 —
# CAN-SPAM이 명시적으로 요구하고, 없으면 어느 실체가 보냈는지 알 수 없다.
REQUIRED = ("legal_name", "postal_address", "contact_email")


def missing_fields(identity: dict) -> list:
    """고지를 만들 수 없게 하는 빈칸. 비어 있으면 발송을 막는 근거가 된다."""
    d = identity or {}
    return [k for k in REQUIRED if not str(d.get(k) or "").strip()]


def law_for(country: str) -> str:
    """수신 국가의 준거 법령. 모르면 빈 문자열 — 아무 법이나 대지 않는다."""
    return _LAW.get((country or "").strip().upper(), "")


def footer(identity: dict, *, language: str, country: str = "") -> str:
    """본문 끝에 붙일 고지. 필수 정보가 없으면 빈 문자열을 돌려준다 —
    부분적으로 채운 고지는 고지가 아니라 위장이다."""
    if missing_fields(identity):
        return ""
    lang = (language or "en").split("-")[0].lower()
    notice = _NOTICE.get(lang) or _NOTICE["en"]
    law = law_for(country)
    if law:
        notice = f"[{law}] {notice}"
    lines = [
        "—",
        str(identity["legal_name"]).strip(),
        str(identity["postal_address"]).strip(),
        str(identity["contact_email"]).strip(),
    ]
    for opt in ("phone", "website"):
        v = str((identity or {}).get(opt) or "").strip()
        if v:
            lines.append(v)
    url = str((identity or {}).get("unsubscribe_url") or "").strip()
    if url:
        notice += f" ({url})"
    lines.append("")
    lines.append(notice)
    return "\n".join(lines)
