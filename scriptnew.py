import os
import shutil
import stat
from urllib.parse import urlparse
from config_reader import ConfigReader
from githelper import GitHubClient
from logger import Logger
from svn import SvnUploader
from extract_utils import ArchiveExtractor


# ---------------------------------------------------------
# Helper: Fix Windows read-only errors when deleting SVN
# ---------------------------------------------------------
def remove_readonly(func, path, excinfo):
    os.chmod(path, stat.S_IWRITE)
    func(path)


# ---------------------------------------------------------
# Helper: Build fast lookup map (filename → full path)
# ---------------------------------------------------------
def build_source_map(file_list):
    source_map = {}
    for f in file_list:
        source_map[os.path.basename(f)] = f
    return source_map


class FileReplacer:

    def __init__(self, base_root: str, denso_path: str, rules: dict,
                 output_folder: str, guest_url: str, host_url: str):

        self.base_root = base_root
        self.denso_path = denso_path
        self.rules = rules
        self.output_folder = output_folder
        self.guest_url = guest_url
        self.host_url = host_url

        self.log = Logger.get_logger("Replace")
        self.extractor = ArchiveExtractor()
        self.github = GitHubClient()

        self.denso_folder = None

    # ---------------------------------------------------------
    # STEP 1: Download GitHub artifacts
    # ---------------------------------------------------------
    def download_github(self) -> bool:
        try:
            owner, repo, runid = self._parse_runid(self.guest_url)
            self.github.download_run_id_artifacts(owner, repo, [runid], self.output_folder)

            owner, repo, runid = self._parse_runid(self.host_url)
            self.github.download_run_id_artifacts(owner, repo, [runid], self.output_folder)

            return True
        except Exception as e:
            self.log.error(f"[CRITICAL] GitHub download failed: {e}")
            return False

    def _parse_runid(self, url: str):
        if not url or not isinstance(url, str):
            raise ValueError("Invalid URL: empty or not a string")

        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]

        if len(parts) < 5:
                raise ValueError(f"Invalid GitHub Actions URL format: {url}")

        owner = parts[0]
        repo = parts[1]
        runid = parts[-1]

        if not runid.isdigit():
                    raise ValueError(f"Invalid run ID in URL: {runid}")

        return owner, repo, runid

    # ---------------------------------------------------------
    # STEP 2: Checkout SVN
    # ---------------------------------------------------------
    def svn_artifact_download(self) -> bool:
        try:
            cfg = ConfigReader("config.toml").get_section("svn")

            svn = SvnUploader(
                repo_url=cfg["repo_url"],
                username=cfg["username"],
                password=cfg["password"]
            )

            checkout_dir = cfg["checkout_dir"]

            # Delete existing SVN folder safely
            if os.path.exists(checkout_dir):
                self.log.info(f"Removing existing SVN folder: {checkout_dir}")
                

            self.log.info(f"Checking out SVN → {checkout_dir}")
            svn._checkout(checkout_dir, cfg["repo_url"])
            self.log.info("SVN checkout completed")

            return True

        except Exception as e:
            self.log.error(f"[CRITICAL] SVN checkout failed: {e}")
            return False

   

    # ---------------------------------------------------------
    # STEP : Build source file list 
    # ---------------------------------------------------------
    def get_source_files(self):
        source_list = []

        for root, dirs, files in os.walk(self.base_root):
            for filename in files:
                full_path = os.path.join(root, filename)

                if filename.lower().endswith(".zip"):
                    extract_dir = os.path.splitext(full_path)[0]
                    self.log.info(f"Extracting ZIP: {full_path} → {extract_dir}")

                    self.extractor.extract_file(full_path, extract_dir)

                    for r, d, extracted_files in os.walk(extract_dir):
                        for extracted in extracted_files:
                            source_list.append(os.path.join(r, extracted))

                else:
                    source_list.append(full_path)

        return source_list

    # ---------------------------------------------------------
    # STEP 5: Apply replacement rules
    # ---------------------------------------------------------
    def apply_rules(self, file_list):
        source_map = build_source_map(file_list)

        for rule_name, rule_data in self.rules.items():

            sources = rule_data.get("source", [])
            dest_folder = rule_data.get("destination")

            if not isinstance(sources, list):
                self.log.error(f"Invalid source list in rule '{rule_name}'")
                continue

            for src_name in sources:

                src_path = source_map.get(os.path.basename(src_name))
                if not src_path:
                    self.log.error(f"[{rule_name}] Source file not found: {src_name}")
                    continue

                dest_path = os.path.join(self.denso_folder, dest_folder, os.path.basename(src_name))

                try:
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

                    if os.path.exists(dest_path):
                        os.remove(dest_path)

                    self.log.info(f"[{rule_name}] Copying {src_path} → {dest_path}")
                    shutil.copy2(src_path, dest_path)

                except Exception as e:
                    self.log.error(f"[{rule_name}] Failed to copy {src_path} to {dest_path}: {e}")

        return True

    # ---------------------------------------------------------
    # MAIN RUN
    # ---------------------------------------------------------
    def run(self) -> bool:
        self.log.info("Replace started")

        if not self.download_github():
            return False

        if not self.svn_artifact_download():
            return False
      
        file_list = self.get_source_files()
        if not file_list:
            self.log.error("No source files found")
            return False

        if not self.apply_rules(file_list):
            return False

        self.log.info("Replace completed successfully")
        return True


# ---------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------
if __name__ == "__main__":
    config_reader = ConfigReader("config.toml")
    cfg = config_reader.get_section("tool.file_replace")

    replacer = FileReplacer(
        base_root=cfg["base_root"],
        denso_path=cfg["Denso_folder"],
        rules=cfg["rules"],
        output_folder=cfg["output_folder"],
        guest_url=cfg["Guest_runid"],
        host_url=cfg["Host_runid"],
    )

    replacer.run()

