from __future__ import annotations

import errno
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from offline import run_offline_acceptance as acceptance


class OfflineAcceptanceTests(unittest.TestCase):
    def test_denied_phase_covers_every_claimed_operation_in_order(self) -> None:
        self.assertEqual(
            acceptance.ONLINE_TASKS,
            (
                "verify-runtime",
                "assets-hydrate",
                "assets-verify",
                "build-browser-pages",
            ),
        )
        self.assertEqual(
            acceptance.DENIED_TASKS,
            (
                "assets-verify",
                "validate",
                "projection-verify",
                "hugo-projection-update",
                "projection-verify",
                "build",
                "build-repeat",
            ),
        )

    def test_linux_boundary_requires_a_distinct_route_free_namespace(self) -> None:
        acceptance.validate_linux_namespace(
            "net:[1]",
            "net:[2]",
            {"lo"},
            ["Iface Destination Gateway"],
        )
        failures = (
            ("net:[1]", "net:[1]", {"lo"}, ["header"], "new network"),
            ("net:[1]", "net:[2]", {"lo", "eth0"}, ["header"], "loopback"),
            (
                "net:[1]",
                "net:[2]",
                {"lo"},
                ["header", "eth0 00000000 01010101"],
                "route",
            ),
        )
        for host, current, interfaces, routes, message in failures:
            with self.subTest(message=message):
                with self.assertRaisesRegex(acceptance.AcceptanceError, message):
                    acceptance.validate_linux_namespace(
                        host,
                        current,
                        interfaces,
                        routes,
                    )

    def test_macos_boundary_requires_an_os_policy_denial(self) -> None:
        class DeniedSocket:
            def sendto(self, _payload, _address):
                raise PermissionError(errno.EPERM, "denied")

            def close(self):
                pass

        class OpenSocket(DeniedSocket):
            def sendto(self, payload, _address):
                return len(payload)

        with (
            patch.object(acceptance.platform, "system", return_value="Darwin"),
            patch.object(acceptance.socket, "socket", return_value=DeniedSocket()),
        ):
            acceptance._prove_macos_network_deny()
        with (
            patch.object(acceptance.platform, "system", return_value="Darwin"),
            patch.object(acceptance.socket, "socket", return_value=OpenSocket()),
            self.assertRaisesRegex(acceptance.AcceptanceError, "succeeded"),
        ):
            acceptance._prove_macos_network_deny()

    def test_editor_fixture_is_bound_to_head_path_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = "site-specific/metadata/records/XYZPerson/person.yaml"
            source = root / source_path
            source.parent.mkdir(parents=True)
            source.write_text(
                "pid: example:person\n"
                "schema_type: example:Person\n"
                "given_name: Original\n",
                encoding="utf-8",
            )
            contract_path = root / acceptance.CONTRACT_RELATIVE
            contract_path.parent.mkdir(parents=True)
            contract_path.write_text(
                json.dumps(
                    {
                        "review_bundle": {
                            "format": "orinoco-shacl-review-bundle",
                            "version": 2,
                        },
                        "test_record": {
                            "edited_given_name": "Offline Review",
                            "pid": "example:person",
                            "source_path": source_path,
                        },
                    }
                ),
                encoding="utf-8",
            )
            head = "a" * 40
            catalog_path = root / acceptance.CATALOG_RELATIVE
            catalog_path.parent.mkdir(parents=True)
            catalog_path.write_text(
                json.dumps(
                    {
                        "source_commit": head,
                        "records": [
                            {
                                "path": source_path,
                                "pid": "example:person",
                                "rdf_turtle": (
                                    "<https://example.test/person> "
                                    "<https://example.test/v2/given_name> "
                                    '"Original" .\n'
                                ),
                                "schema_type": "example:Person",
                                "sha256": hashlib.sha256(
                                    source.read_bytes()
                                ).hexdigest(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            bundle_path, observed_source = acceptance.construct_editor_bundle(
                root,
                head=head,
            )
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            self.assertEqual(source_path, observed_source)
            self.assertEqual(head, bundle["source_commit"])
            self.assertIn("Offline Review", bundle["records"][0]["rdf_turtle"])
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(),
                bundle["records"][0]["source_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
