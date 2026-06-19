"""Tests for the Host/Fleet model and the CSV/YAML loaders."""

from __future__ import annotations

from pathlib import Path

import pytest

from yuj.exceptions import FleetError
from yuj.fleet import Fleet, Host, load_from_csv, load_from_yaml


class TestHost:
    def test_password_not_in_repr(self) -> None:
        host = Host(name="h", ip="1.2.3.4", user="u", password="hunter2")
        text = repr(host)
        assert "hunter2" not in text
        assert "***" in text

    def test_none_password_renders_as_none(self) -> None:
        host = Host(name="h", ip="1.2.3.4", user="u", key_path="/k")
        assert "password=None" in repr(host)

    def test_auth_kind_key_beats_password(self) -> None:
        host = Host(name="h", ip="1", user="u", password="p", key_path="/k")
        assert host.auth_kind == "key"
        assert host.use_password is False

    def test_auth_kind_password(self) -> None:
        host = Host(name="h", ip="1", user="u", password="p")
        assert host.auth_kind == "password"
        assert host.use_password is True

    def test_auth_kind_none(self) -> None:
        host = Host(name="h", ip="1", user="u")
        assert host.auth_kind == "none"
        assert host.use_password is False

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(FleetError, match="negative weight"):
            Host(name="h", ip="1", user="u", weight=-1.0)

    @pytest.mark.parametrize("port", [0, -1, 70000])
    def test_invalid_port_raises(self, port: int) -> None:
        with pytest.raises(FleetError, match="invalid port"):
            Host(name="h", ip="1", user="u", port=port)

    def test_is_frozen(self) -> None:
        host = Host(name="h", ip="1", user="u")
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            host.ip = "2"  # type: ignore[misc]


class TestFleet:
    def _fleet(self) -> Fleet:
        return Fleet(
            (
                Host(name="a", ip="1", user="u", weight=1.0),
                Host(name="b", ip="2", user="u", weight=3.0),
            )
        )

    def test_len_iter_bool(self) -> None:
        fleet = self._fleet()
        assert len(fleet) == 2
        assert [h.name for h in fleet] == ["a", "b"]
        assert bool(fleet) is True
        assert bool(Fleet(())) is False

    def test_names_and_total_weight(self) -> None:
        fleet = self._fleet()
        assert fleet.names == ("a", "b")
        assert fleet.total_weight == 4.0

    def test_get_found_and_missing(self) -> None:
        fleet = self._fleet()
        assert fleet.get("a").ip == "1"
        with pytest.raises(FleetError, match="no host named"):
            fleet.get("zzz")

    def test_select_subset_preserves_order(self) -> None:
        fleet = self._fleet()
        sub = fleet.select(["b"])
        assert sub.names == ("b",)

    def test_select_unknown_raises(self) -> None:
        with pytest.raises(FleetError, match="unknown hosts"):
            self._fleet().select(["nope"])

    def test_with_weights_overrides(self) -> None:
        fleet = self._fleet().with_weights({"a": 10.0})
        assert fleet.get("a").weight == 10.0
        assert fleet.get("b").weight == 3.0

    def test_with_weights_unknown_raises(self) -> None:
        with pytest.raises(FleetError, match="unknown hosts"):
            self._fleet().with_weights({"nope": 1.0})

    def test_duplicate_names_raise(self) -> None:
        with pytest.raises(FleetError, match="duplicate host names"):
            Fleet((Host(name="x", ip="1", user="u"), Host(name="x", ip="2", user="u")))

    def test_usable_excludes_do_not_use(self) -> None:
        fleet = Fleet(
            (
                Host(name="ok", ip="1", user="u"),
                Host(name="dead", ip="2", user="u", do_not_use=True),
            )
        )
        assert fleet.usable.names == ("ok",)


class TestDoNotUse:
    def test_csv_parses_do_not_use(self, tmp_path: Path) -> None:
        csv = tmp_path / "fleet.csv"
        csv.write_text(
            "username,ip,name,password,do_not_use\nu,1,ok,p,false\nu,2,dead,p,true\n"
        )
        fleet = load_from_csv(csv)
        assert fleet.get("ok").do_not_use is False
        assert fleet.get("dead").do_not_use is True

    def test_do_not_use_in_repr_but_not_password(self) -> None:
        host = Host(name="h", ip="1", user="u", password="sekret123", do_not_use=True)
        assert "do_not_use=True" in repr(host)
        assert "sekret123" not in repr(host)

    def test_yaml_parses_do_not_use(self, tmp_path: Path) -> None:
        yml = tmp_path / "fleet.yaml"
        yml.write_text(
            "user: u\nmachines:\n"
            "  - name: a\n    ip: 1\n"
            "  - name: b\n    ip: 2\n    do_not_use: true\n"
        )
        fleet = load_from_yaml(yml)
        assert fleet.get("a").do_not_use is False
        assert fleet.get("b").do_not_use is True


class TestLoadFromCsv:
    def test_canonical_format(self, tmp_path: Path) -> None:
        csv = tmp_path / "fleet.csv"
        csv.write_text(
            "username,ip,machine,password\n"
            "saket,10.0.0.1,alpha,secret1\n"
            "saket,10.0.0.2,bravo,secret2\n"
        )
        fleet = load_from_csv(csv)
        assert fleet.names == ("alpha", "bravo")
        assert fleet.get("alpha").user == "saket"
        assert fleet.get("alpha").use_password is True

    def test_optional_columns(self, tmp_path: Path) -> None:
        csv = tmp_path / "fleet.csv"
        csv.write_text(
            "user,host,name,key_path,weight,port\n"
            "me,1.1.1.1,box,/home/me/.ssh/id_ed25519,2.5,2222\n"
        )
        fleet = load_from_csv(csv)
        host = fleet.get("box")
        assert host.key_path == "/home/me/.ssh/id_ed25519"
        assert host.weight == 2.5
        assert host.port == 2222
        assert host.auth_kind == "key"

    def test_strict_host_key_columns(self, tmp_path: Path) -> None:
        csv = tmp_path / "fleet.csv"
        csv.write_text(
            "user,host,name,key_path,strict_host_key,known_hosts_file\n"
            "me,1.1.1.1,box,/home/me/.ssh/id_ed25519,true,/tmp/known_hosts\n"
        )
        host = load_from_csv(csv).get("box")
        assert host.strict_host_key is True
        assert host.known_hosts_file == "/tmp/known_hosts"

    def test_skips_blank_named_rows(self, tmp_path: Path) -> None:
        csv = tmp_path / "fleet.csv"
        csv.write_text("username,ip,name,password\nu,1,good,p\nu,2,,p\n")
        fleet = load_from_csv(csv)
        assert fleet.names == ("good",)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FleetError, match="not found"):
            load_from_csv(tmp_path / "nope.csv")

    def test_missing_required_columns_raises(self, tmp_path: Path) -> None:
        csv = tmp_path / "fleet.csv"
        csv.write_text("foo,bar\n1,2\n")
        with pytest.raises(FleetError, match="missing required columns"):
            load_from_csv(csv)

    def test_no_usable_hosts_raises(self, tmp_path: Path) -> None:
        csv = tmp_path / "fleet.csv"
        csv.write_text("username,ip,name,password\n")
        with pytest.raises(FleetError, match="no usable hosts"):
            load_from_csv(csv)

    def test_bad_numeric_raises(self, tmp_path: Path) -> None:
        csv = tmp_path / "fleet.csv"
        csv.write_text("user,ip,name,weight\nu,1,box,notanumber\n")
        with pytest.raises(FleetError, match="bad numeric"):
            load_from_csv(csv)

    def test_truly_empty_file_raises(self, tmp_path: Path) -> None:
        csv = tmp_path / "fleet.csv"
        csv.write_text("")
        with pytest.raises(FleetError, match="empty"):
            load_from_csv(csv)


class TestLoadFromYaml:
    def test_defaults_and_overrides(self, tmp_path: Path) -> None:
        yml = tmp_path / "fleet.yaml"
        yml.write_text(
            "user: saket\n"
            "port: 22\n"
            "weight: 1.0\n"
            "machines:\n"
            "  - name: a\n"
            "    ip: 10.0.0.1\n"
            "  - name: b\n"
            "    ip: 10.0.0.2\n"
            "    user: other\n"
            "    weight: 5\n"
            "    port: 2200\n"
        )
        fleet = load_from_yaml(yml)
        assert fleet.names == ("a", "b")
        assert fleet.get("a").user == "saket"
        assert fleet.get("b").user == "other"
        assert fleet.get("b").weight == 5.0
        assert fleet.get("b").port == 2200

    def test_strict_host_key_defaults_and_overrides(self, tmp_path: Path) -> None:
        yml = tmp_path / "fleet.yaml"
        yml.write_text(
            "user: u\n"
            "strict_host_key: true\n"
            "known_hosts_file: /tmp/known_hosts\n"
            "machines:\n"
            "  - name: a\n"
            "    ip: 1\n"
            "  - name: b\n"
            "    ip: 2\n"
            "    strict_host_key: false\n"
        )
        fleet = load_from_yaml(yml)
        assert fleet.get("a").strict_host_key is True
        assert fleet.get("a").known_hosts_file == "/tmp/known_hosts"
        assert fleet.get("b").strict_host_key is False

    def test_host_key_alias_for_ip(self, tmp_path: Path) -> None:
        yml = tmp_path / "fleet.yaml"
        yml.write_text("user: u\nmachines:\n  - name: a\n    host: 9.9.9.9\n")
        assert load_from_yaml(yml).get("a").ip == "9.9.9.9"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FleetError, match="not found"):
            load_from_yaml(tmp_path / "nope.yaml")

    def test_no_machines_key_raises(self, tmp_path: Path) -> None:
        yml = tmp_path / "fleet.yaml"
        yml.write_text("user: u\n")
        with pytest.raises(FleetError, match="must be a mapping with a 'machines'"):
            load_from_yaml(yml)

    def test_machine_missing_user_raises(self, tmp_path: Path) -> None:
        yml = tmp_path / "fleet.yaml"
        yml.write_text("machines:\n  - name: a\n    ip: 1.1.1.1\n")
        with pytest.raises(FleetError, match="needs name, ip, and user"):
            load_from_yaml(yml)

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        yml = tmp_path / "fleet.yaml"
        yml.write_text("machines: [unclosed\n")
        with pytest.raises(FleetError, match="could not parse"):
            load_from_yaml(yml)

    def test_empty_machines_list_raises(self, tmp_path: Path) -> None:
        yml = tmp_path / "fleet.yaml"
        yml.write_text("user: u\nmachines: []\n")
        with pytest.raises(FleetError, match="no machines listed"):
            load_from_yaml(yml)

    def test_machines_not_a_list_raises(self, tmp_path: Path) -> None:
        yml = tmp_path / "fleet.yaml"
        yml.write_text("user: u\nmachines:\n  a: 1\n")
        with pytest.raises(FleetError, match="must be a list"):
            load_from_yaml(yml)

    def test_machine_entry_not_a_mapping_raises(self, tmp_path: Path) -> None:
        yml = tmp_path / "fleet.yaml"
        yml.write_text("user: u\nmachines:\n  - just-a-string\n")
        with pytest.raises(FleetError, match="is not a mapping"):
            load_from_yaml(yml)
