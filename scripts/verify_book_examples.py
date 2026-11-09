#!/usr/bin/env python3
"""Compile complete C# exercise snippets in disposable temporary projects.

Runs dotnet from samples so global.json selects the documented SDK.
Does not create MemoryScratch/NetworkScratch in the checkout.
"""
import html
import pathlib
import re
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def block(path, language, contains):
    text = path.read_text(encoding="utf-8")
    matches = [source for source in re.findall(rf"```{language}\n(.*?)\n```", text, re.S) if contains in source]
    if len(matches) != 1:
        raise ValueError(f"Expected one matching {language} block in {path.name}, got {len(matches)}")
    return matches[0] + "\n"


def run(project):
    result = subprocess.run(
        ["dotnet", "run", "--project", str(project), "-c", "Release", "--nologo"],
        cwd=SAMPLES, text=True, capture_output=True, timeout=60,
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def main():
    memory = block(ROOT / "docs/book/part-2/07-memory.md", "csharp", "ReadOnlyMemory<byte> view")
    exercise = ROOT / "docs/book/part-4/20-exercises.md"
    pipeline = block(exercise, "csharp", "var pipe = new Pipe();")
    project = block(exercise, "xml", '<Project Sdk="Microsoft.NET.Sdk">')
    legacy = block(exercise, "xml", 'Link="Legacy/RedisRequest.cs"')
    # Preserve the documented structure but resolve repository-relative paths from a temporary directory.
    gateway = html.escape(str(SAMPLES / "IndustrialGateway/IndustrialGateway.csproj"), quote=True)
    project = project.replace("../IndustrialGateway/IndustrialGateway.csproj", gateway)
    for filename in ("RedisRequest.cs", "RedisValue.cs", "RedisCmd.cs", "RedisProtocolException.cs"):
        location = html.escape(str(ROOT / "KestrelApp.Middleware/Redis" / filename), quote=True)
        legacy = legacy.replace(f"../../KestrelApp.Middleware/Redis/{filename}", location)
    with tempfile.TemporaryDirectory(prefix="kestrel-book-exercises-") as directory:
        target = pathlib.Path(directory).resolve()
        # Verify the only auto-cleaned directory is the temporary project we just created.
        assert target.parent == pathlib.Path(tempfile.gettempdir()).resolve()
        assert target.name.startswith("kestrel-book-exercises-")
        csproj = target / "Exercises.csproj"
        source = target / "Program.cs"
        csproj.write_text(project, encoding="utf-8")
        source.write_text(memory, encoding="utf-8")
        output = run(csproj)
        assert "view[0] = 99" in output and "copy[0] = 2" in output, output
        print("PASS chapter 7 complete memory snippet")
        source.write_text(pipeline, encoding="utf-8")
        output = run(csproj)
        assert "第一次可读长度：2" in output and "等待更多输入：True" in output and "010203" in output, output
        print("PASS chapter 20 complete Pipe workspace and snippet")
        csproj.write_text(project.replace("</Project>", legacy + "</Project>"), encoding="utf-8")
        source.write_text('''using System.Buffers;
using System.Text;
using KestrelApp.Middleware.Redis;
var bytes = Encoding.ASCII.GetBytes("*1\\r\\n$4\\r\\nPING\\r\\n");
var requests = RedisRequest.Parse(new ReadOnlySequence<byte>(bytes), out _);
if (requests.Count != 1 || requests[0].Cmd != RedisCmd.Ping) throw new Exception("RESP host check");
Console.WriteLine("RESP_HOST_OK");
''', encoding="utf-8")
        output = run(csproj)
        assert "RESP_HOST_OK" in output, output
        print("PASS chapter 20 linked legacy parser builds offline on net10")
    print("PASS all complete exercise snippets; temporary workspace removed")


if __name__ == "__main__":
    main()
