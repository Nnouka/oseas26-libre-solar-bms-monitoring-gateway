#!/usr/bin/env python3
"""Install the Libre Solar BMS plugin into a MicroPowerManager checkout.

Usage: python apply.py <path-to-mpm-repo>

Copies backend/ and frontend/ into <mpm>/src/ and patches four core files.
Safe to run again: files are overwritten, patches are skipped if already present.
"""
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ID = 33


def patch(path: Path, marker: str, edit) -> str:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return f"already patched: {path.name}"
    new = edit(text)
    if new == text:
        raise SystemExit(f"could not patch {path} (anchor not found). MPM may have changed.")
    path.write_text(new, encoding="utf-8", newline="\n")
    return f"patched: {path.name}"


def plugin_id(text):
    return re.sub(
        r"(    public const FLUTTERWAVE_PAYMENT_PROVIDER = \d+;\n)",
        rf"\1    public const LIBRE_SOLAR_BMS = {PLUGIN_ID};\n",
        text, count=1)


def providers(text):
    use = "use App\\Plugins\\LibreSolarBms\\Providers\\LibreSolarBmsServiceProvider;\n"
    lines = text.splitlines(keepends=True)
    out, done = [], False
    for line in lines:
        if not done and line.startswith("use App\\Plugins\\") and line > use:
            out.append(use)
            done = True
        out.append(line)
    text = "".join(out)
    return text.replace("    DalyBmsServiceProvider::class,\n",
                        "    DalyBmsServiceProvider::class,\n    LibreSolarBmsServiceProvider::class,\n", 1)


def resolver(text):
    text = text.replace(
        "    public const EXTERNAL_TRANSACTIONS_API = 'api/appliances/payment/third-party';\n",
        "    public const EXTERNAL_TRANSACTIONS_API = 'api/appliances/payment/third-party';\n"
        "    public const LIBRE_SOLAR_BMS_INGEST_API = 'api/libre-solar-bms/ingest/';\n", 1)
    return text.replace(
        "        self::EXTERNAL_TRANSACTIONS_API => ExternalTransactionApiResolver::class,\n",
        "        self::EXTERNAL_TRANSACTIONS_API => ExternalTransactionApiResolver::class,\n"
        "        self::LIBRE_SOLAR_BMS_INGEST_API => ExternalTransactionApiResolver::class,\n", 1)


ROUTE = '''  {
    path: "/libre-solar-bms",
    component: ChildRouteWrapper,
    meta: {
      layout: "default",
      sidebar: {
        enabled_by_mpm_plugin_id: %d,
        name: "Battery Health",
        icon: "battery_charging_full",
      },
    },
    children: [
      {
        path: "batteries",
        component: ChildRouteWrapper,
        children: [
          {
            path: "",
            component: LibreSolarBmsOverview,
            meta: {
              layout: "default",
              sidebar: { enabled: true, name: "Batteries" },
              breadcrumb: { level: "base", name: "Batteries", link: "/libre-solar-bms/batteries" },
            },
          },
          {
            path: ":id",
            component: LibreSolarBmsBatteryDetail,
            meta: {
              layout: "default",
              breadcrumb: {
                level: "detail",
                name: "Batteries",
                link: "/libre-solar-bms/batteries",
                target: "id",
              },
            },
          },
        ],
      },
    ],
  },
''' % PLUGIN_ID


def routes(text):
    imports = [
        'import LibreSolarBmsBatteryDetail from "@/plugins/libre-solar-bms/modules/Battery/BatteryDetail.vue"\n',
        'import LibreSolarBmsOverview from "@/plugins/libre-solar-bms/modules/Overview/Overview.vue"\n',
    ]
    lines = text.splitlines(keepends=True)
    out, done = [], False
    for line in lines:
        m = re.search(r'from "@/plugins/([^"]+)"', line)
        if not done and line.startswith("import ") and m and m.group(1) > "libre-solar-bms/":
            out.extend(imports)
            done = True
        out.append(line)
    text = "".join(out)
    return text.replace('  {\n    path: "/angaza-shs",', ROUTE + '  {\n    path: "/angaza-shs",', 1)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    mpm = Path(sys.argv[1]).resolve()
    backend, frontend = mpm / "src" / "backend", mpm / "src" / "frontend"
    if not (backend / "app" / "Plugins").is_dir() or not (frontend / "src").is_dir():
        raise SystemExit(f"{mpm} does not look like a MicroPowerManager checkout")

    shutil.copytree(HERE / "backend", backend, dirs_exist_ok=True)
    shutil.copytree(HERE / "frontend", frontend, dirs_exist_ok=True)
    print("copied plugin files")

    print(patch(backend / "app/Models/MpmPlugin.php", "LIBRE_SOLAR_BMS", plugin_id))
    print(patch(backend / "bootstrap/providers.php", "LibreSolarBmsServiceProvider", providers))
    print(patch(backend / "app/Services/ApiResolvers/ThirdPartyApiResolverService.php",
                "LIBRE_SOLAR_BMS_INGEST_API", resolver))
    print(patch(frontend / "src/ExportedRoutes.js", "LibreSolarBmsOverview", routes))
    print("\nNext: php artisan migrate && php artisan migrate-tenant")


if __name__ == "__main__":
    main()
