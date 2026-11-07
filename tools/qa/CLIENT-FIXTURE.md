# Independent TFR client

`client_fixture.py` prepares an opt-in Windows QA instance from this source
repository and an existing installed Minecraft runtime. It does not read personal
instances, settings, maps or worlds. The source files are independently copied
and SHA256 checked. Publication config projections are applied to the copy.

Run the workspace storage check before preparation. Choose one owned directory
under `artifacts/agent-runs` and keep its generated `RUN.json` current.

```powershell
python -B tools/qa/client_fixture.py prepare --source . --cache-root <launcher-root> --run <launcher-root>/artifacts/agent-runs/<task-id>
python -B tools/qa/client_fixture.py launch --run <launcher-root>/artifacts/agent-runs/<task-id>
```

The helper supports the pinned Minecraft 1.21.1, NeoForge 21.1.248 and Temurin
21.0.12.1+1 profile. It validates library sizes and SHA1 against both installed
profiles and records SHA256 of those inputs. It validates the asset index and
Java receipt/release. Existing Java, libraries and assets are referenced in
place. The native extraction directory is copied. Asset object bytes and Java
archive bytes are not rehashed by this tool.

`launch.json` contains the native metadata arguments with an offline technical
identity. `client.args` uses Java argument-file syntax, so the long classpath
does not pass through a shell or exceed the Win32 command-line limit. The game
directory, Java home and temporary directory are inside the marked run.
`console.log` and `process.json` belong to that run. The game window is visible,
and the Java console is hidden.

Create only disposable QA worlds through the client. This direct native launch
does not include the launcher's Steam identity adapter and cannot establish
two-player Steam or updater acceptance. Stop the recorded process through the
game, retain compact evidence, and remove the recoverable fixture after its
acceptance work finishes.
