
import os
import re
import shutil
import zipfile
import subprocess
from pathlib import Path

ROOT = Path.cwd()
INPUT = ROOT / "plugins"
WORK = ROOT / "work"
OUTPUT = ROOT / "output"
REPORTS = ROOT / "reports"

for folder in (WORK, OUTPUT, REPORTS):
    folder.mkdir(exist_ok=True, parents=True)

def safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:100]

def unique_copy(source, prefix):
    name = safe_name(prefix + "-" + source.name)
    target = OUTPUT / name
    i = 2
    while target.exists():
        target = OUTPUT / f"{target.stem}-{i}{target.suffix}"
        i += 1
    shutil.copy2(source, target)

def extract(archive, destination):
    total = 0
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            name = item.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("Unsafe ZIP path: " + name)
            if item.file_size > 512 * 1024 * 1024:
                raise ValueError("ZIP entry too large")
            total += item.file_size
            if total > 2 * 1024 * 1024 * 1024:
                raise ValueError("ZIP too large")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                continue
        z.extractall(destination)

def discover(folder):
    projects = []
    for current, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in (
            ".git", ".gradle", "node_modules", "__MACOSX"
        )]

        if "gradlew" in files or "build.gradle" in files or "build.gradle.kts" in files:
            projects.append((Path(current), "gradle"))
        elif "pom.xml" in files:
            projects.append((Path(current), "maven"))

    return projects

def build(project, kind):
    java_version = "17"
    config = ""
    for filename in (
        "build.gradle", "build.gradle.kts",
        "pom.xml", "gradle.properties"
    ):
        file = project / filename
        if file.exists():
            config += file.read_text(errors="ignore")

    if re.search(r"VERSION_21|JavaLanguageVersion.of\(21\)|<maven.compiler.release>21", config):
        java_version = "21"

    env = os.environ.copy()
    java_home = env.get(f"JAVA_HOME_{java_version}_X64")
    if java_home:
        env["JAVA_HOME"] = java_home
        env["PATH"] = java_home + "/bin:" + env["PATH"]

    if kind == "gradle":
        wrapper = project / "gradlew"
        if wrapper.exists():
            wrapper.chmod(0o755)
            command = [str(wrapper), "build", "--no-daemon"]
        else:
            command = ["gradle", "build", "--no-daemon"]
    else:
        command = ["mvn", "-B", "package", "-DskipTests"]

    result = subprocess.run(
        command,
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=1800
    )

    return result.returncode == 0, result.stdout

def main():
    archives = list(INPUT.rglob("*.zip")) if INPUT.exists() else []
    archives += list(ROOT.glob("*.zip"))
    archives = list(dict.fromkeys(p.resolve() for p in archives))

    report = ["# GreenLemon Build Report"]
    count = 0

    for archive in archives:
        name = safe_name(archive.stem)
        print(f"\nProcessing: {archive.name}")

        folder = WORK / name
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)

        try:
            extract(archive, folder)
        except Exception as error:
            report.append(f"- {archive.name}: EXTRACTION FAILED: {error}")
            continue

        # Collect existing JARs
        for jar in folder.rglob("*.jar"):
            if not any(x in jar.parts for x in (".git", ".gradle")):
                unique_copy(jar, name + "-existing")
                count += 1

        projects = discover(folder)
        if not projects:
            java_count = len(list(folder.rglob("*.java")))
            report.append(
                f"- {archive.name}: No build project. Java files found: {java_count}"
            )
            continue

        for project, kind in projects:
            print(f"Building {project} ({kind})")
            try:
                success, log = build(project, kind)
            except Exception as error:
                success, log = False, str(error)

            report.append(
                f"- {archive.name} / {project.relative_to(folder)}: "
                + ("SUCCESS" if success else "FAILED")
            )

            if not success:
                log_file = REPORTS / f"{name}-{safe_name(project.name)}.log"
                log_file.write_text(log, encoding="utf-8")
                continue

            for output_folder in ("build/libs", "target"):
                location = project / output_folder
                if not location.exists():
                    continue

                for jar in location.glob("*.jar"):
                    if jar.name.endswith(("-sources.jar", "-javadoc.jar", "-tests.jar")):
                        continue
                    unique_copy(jar, name)
                    count += 1

    report.append(f"\nTotal JAR files: {count}")
    (REPORTS / "build-report.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(f"Finished. JAR count: {count}")

if __name__ == "__main__":
    main()
          
