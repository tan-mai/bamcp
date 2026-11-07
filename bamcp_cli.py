# TM - #DASHBOARD - Setup dashboard
"""Lenh quan tri chay tay. Trong Docker da co san lenh `bamcp`:

  docker exec -it bamcp bamcp dashboard create-owner <username>

Ngoai Docker: python bamcp_cli.py dashboard create-owner <username>

Dung du phong khi mat quyen vao trang admin. Username da co thi khoi phuc thanh owner dang
hoat dong, dat lai mat khau va dang xuat moi phien cua tai khoan do.
"""
from __future__ import annotations

import argparse
import getpass
import sys


def _read_password(args: argparse.Namespace) -> tuple[str, bool]:
    """Tra ve (mat khau, co phai tu sinh). Khong nhan mat khau qua doi so dong lenh: lo trong ps."""
    import dashboard_auth

    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n"), False
    if sys.stdin.isatty():
        first = getpass.getpass("Mat khau moi (bo trong = sinh ngau nhien): ")
        if not first:
            return dashboard_auth.random_password(), True
        if first != getpass.getpass("Nhap lai: "):
            raise ValueError("hai lan nhap khong khop")
        return first, False
    return dashboard_auth.random_password(), True


def create_owner(args: argparse.Namespace) -> int:
    password, generated = _read_password(args)
    import server  # nap sau de --help khong phai doc config

    user, created = server.DASH_AUTH.ensure_owner(args.username, password, actor="cli")
    print(f"Da {'tao' if created else 'khoi phuc'} owner: {user['username']}")
    if generated:
        print(f"Mat khau (chi hien mot lan): {password}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bamcp", description="Lenh quan tri BAMCP")
    groups = parser.add_subparsers(dest="group", required=True)
    dashboard = groups.add_parser("dashboard", help="Tai khoan setup dashboard")
    commands = dashboard.add_subparsers(dest="command", required=True)
    owner = commands.add_parser("create-owner", help="Tao hoac khoi phuc tai khoan owner")
    owner.add_argument("username")
    owner.add_argument("--password-stdin", action="store_true", help="Doc mat khau tu stdin")
    owner.set_defaults(run=create_owner)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except ValueError as exc:
        print(f"Loi: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
