import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.release_channel import (
    ReleaseAsset,
    ReleaseInfo,
    detect_platform_tag,
    install_standalone_binary,
    is_newer_version,
    select_release_asset,
    standalone_archive_name,
)


class ReleaseChannelTest(unittest.TestCase):
    def test_is_newer_version_compares_semver_like_strings(self) -> None:
        self.assertTrue(is_newer_version("1.2.0", "1.1.9"))
        self.assertFalse(is_newer_version("1.2.0", "1.2.0"))
        self.assertFalse(is_newer_version("1.1.9", "1.2.0"))

    def test_select_release_asset_matches_expected_archive_name(self) -> None:
        release = ReleaseInfo(
            tag_name="v1.2.3",
            version="1.2.3",
            assets=(
                ReleaseAsset("issue-to-pr-bot-agent-windows-x64.zip", "https://example.com/agent.zip"),
                ReleaseAsset("issue-to-pr-bot-installer-windows-x64.zip", "https://example.com/installer.zip"),
            ),
        )

        asset = select_release_asset(release, "agent", platform_tag="windows-x64")

        self.assertEqual(asset.name, "issue-to-pr-bot-agent-windows-x64.zip")

    @patch("app.release_channel.fetch_latest_release_info")
    @patch("app.release_channel.download_release_asset")
    def test_install_standalone_binary_extracts_latest_release(
        self,
        mock_download_asset,
        mock_fetch_release,
    ) -> None:
        platform_tag = detect_platform_tag()
        asset_name = standalone_archive_name("agent", platform_tag)
        release = ReleaseInfo(
            tag_name="v1.2.3",
            version="1.2.3",
            assets=(ReleaseAsset(asset_name, "https://example.com/agent"),),
        )
        mock_fetch_release.return_value = release

        def fake_download(_asset: ReleaseAsset, destination: Path, *, timeout: int = 60) -> Path:
            if destination.suffix == ".zip":
                with zipfile.ZipFile(destination, "w") as archive:
                    archive.writestr(
                        "bundle/" + ("issue-to-pr-bot-agent.exe" if platform_tag.startswith("windows-") else "issue-to-pr-bot-agent"),
                        b"binary",
                    )
            else:
                with tarfile.open(destination, "w:gz") as archive:
                    data = b"binary"
                    info = tarfile.TarInfo(
                        name="bundle/" + ("issue-to-pr-bot-agent.exe" if platform_tag.startswith("windows-") else "issue-to-pr-bot-agent")
                    )
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            return destination

        mock_download_asset.side_effect = fake_download

        with tempfile.TemporaryDirectory() as temp_dir:
            installed_path, action, version = install_standalone_binary("agent", Path(temp_dir))

        self.assertEqual(action, "created")
        self.assertEqual(version, "1.2.3")
        self.assertTrue(installed_path.name.startswith("issue-to-pr-bot-agent"))


if __name__ == "__main__":
    unittest.main()
