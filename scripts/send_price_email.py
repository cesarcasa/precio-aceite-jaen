from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path


QUALITY_NAMES = {
    "aove": "AOVE",
    "virgen": "Virgen",
    "lampante": "Lampante",
}
SITE_URL = "https://cesarcasa.github.io/precio-aceite-jaen/"


def euros(value: float) -> str:
    return f"{abs(value):.2f}".replace(".", ",")


def period(year: int, week: int) -> str:
    return f"semana {week} de {year}"


def main() -> None:
    report_path = Path(sys.argv[1] if len(sys.argv) > 1 else "price-update.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT") or "587")
    required_secrets = ("SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_TO")
    missing = [name for name in required_secrets if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Faltan secretos de GitHub Actions: " + ", ".join(missing)
        )
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    recipient = os.environ["EMAIL_TO"]
    sender = os.environ.get("EMAIL_FROM") or username

    current = period(int(report["latestYear"]), int(report["latestWeek"]))
    previous = period(int(report["previousYear"]), int(report["previousWeek"]))
    lines = [
        "Se han actualizado los precios oficiales del aceite de oliva en Jaén.",
        "",
        f"Periodo nuevo: {current}",
        f"Comparación: {previous}",
        "Variación en euros por kilogramo (€/kg):",
        "",
    ]

    for quality, label in QUALITY_NAMES.items():
        price = float(report["latestPrices"][quality])
        change = float(report["changes"][quality])
        sign = "subió" if change > 0 else "bajó" if change < 0 else "no varió"
        lines.append(f"{label}: {price:.2f} €/kg; {sign} {euros(abs(change))} €/kg.")

    lines.extend(["", f"Ver la aplicación: {SITE_URL}", "", "Fuente: Junta de Andalucía · Jaén"])

    message = EmailMessage()
    message["Subject"] = f"Precios del aceite actualizados — {current}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content("\n".join(lines))

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(username, password)
        smtp.send_message(message)

    print(f"Aviso enviado a {recipient} para la {current}.")


if __name__ == "__main__":
    main()
