# devin --help 原文（devin 3000.14.30821，2026-11-21 擷取）

```
A fast and minimal agent that lives both in your terminal and in the cloud.

Usage: devin [OPTIONS] [PATH]... [-- <PROMPT>...] [COMMAND]

Commands:
  auth       Authentication related commands
  mcp        Connect and log in to Model Context Protocol servers
  models     List the models available to your account
  doctor     Diagnose the local Devin configuration
  rules      Manage agent rules (always-on context blobs)
  skills     Manage agent skills (slash commands and agent-triggered context blobs)
  plugins    Manage plugins (install, list, info, update, remove)
  cloud      Start a Devin Cloud session (same as --cloud) or manage cloud resources
  desktop    Open Devin Desktop
  list       List sessions in the current directory [aliases: ls]
  rm         Delete a session by id or name
  ssh        SSH into a cloud Devin session's box
  forward    Forward ports from a cloud Devin session's box to localhost
  update     Check for updates and optionally install them
  version    Print the current version
  migrate    Migrate configuration from other tools
  sandbox    [Research Preview] Process sandboxing for the exec tool
  setup      Interactive setup wizard
  uninstall  Uninstall and remove data
  acp        Run as an ACP (Agent Client Protocol) server over stdio
  daemon     Manage the background agent daemon that `acp` clients share
  help       Print this message or the help of the given subcommand(s)

Arguments:
  [PATH]...
          Open Devin Desktop on the given path(s)

  [PROMPT]...
          Your prompt (use -- before the prompt)
          
          Starts an interactive session. Use -p/--print for non-interactive mode.

Options:
      --prompt-file <FILE>
          Load the initial prompt from a file

      --config <PATH>
          Configuration file path
          
          Override the default user config file (~/.config/devin/config.json).

      --permission-mode <PERMISSION_MODE>
          Permission mode
          
          Modes: "auto" auto-approves read-only tools, "accept-edits" also auto-approves workspace edits, "smart" additionally auto-runs actions a fast model judges safe, "dangerous" auto-approves all tools. Defaults to `permission_mode` from the config file, then "auto".
          
          [env: DEVIN_PERMISSION_MODE=]

      --sandbox
          [Research Preview] Sandbox exec-tool processes (macOS seatbelt / Linux bwrap+seccomp)
          
          When passed, commands can write only within the workspace and granted `Write(...)` scopes, and can read everything except paths hidden by `Deny(Read(...))` rules.
          
          [env: DEVIN_SANDBOX=]

      --cloud
          Drive Devin cloud sessions instead of the local agent
          
          The REPL talks to `devin acp --cloud`, which relays ACP to the Devin cloud ACP WebSocket. Requires a Devin account (sign in with `auth login`).

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

      --model <MODEL>
          Model to use (e.g. "claude-sonnet-4", "claude-opus-4.6", "opus", "codex")
          
          With -c/--continue or -r/--resume, switches the resumed conversation to this model; without it the session's saved model is kept.
          
          [env: DEVIN_MODEL=]

      --verbosity <LEVEL>
          Output detail: 0 quiet, 1 compact, 2 detailed, 3 verbose
          
          Overrides `agent.verbosity` from the config file for this run only.
          
          [env: DEVIN_VERBOSITY=]

  -p, --print [<PROMPT>]
          Print response and exit
          
          Runs in non-interactive mode: processes the prompt and exits. Optionally accepts an inline prompt: -p "fix the bug"

      --export [<PATH>]
          Export conversation to a file
          
          Exports after each turn. Uses a default path if no path is provided.

  -c, --continue
          Continue the most recent conversation

  -r, --resume [<SESSION_ID>]
          Resume a conversation
          
          Provide a session ID to resume a specific session, or omit to select interactively.

      --respect-workspace-trust [<RESPECT_WORKSPACE_TRUST>]
          Respect workspace trust settings
          
          Defaults to true in every mode. Non-interactive (print) mode cannot show the trust prompt and fails in an untrusted directory; pass --respect-workspace-trust false to skip the check. Cloud sessions (`--cloud`) never check trust.
          
          [possible values: true, false]

  -h, --help
          Print help (see a summary with '-h')

  -V, --version
          Print version
=================== devin auth --help
Authentication related commands

Usage: devin auth [OPTIONS] <COMMAND>

Commands:
  login   Log in to Devin
  logout  Log out and remove stored credentials
  status  Check authentication status
  help    Print this message or the help of the given subcommand(s)

Options:
      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin mcp --help
Connect and log in to Model Context Protocol servers

Usage: devin mcp [OPTIONS] <COMMAND>

Commands:
  add      Add a new MCP server
  list     List all configured MCP servers
  get      Get details for a specific MCP server
  remove   Remove an MCP server
  login    Authenticate with an MCP server via OAuth
  logout   Remove stored OAuth credentials for an MCP server
  enable   Enable a disabled MCP server
  disable  Disable an MCP server without removing it
  help     Print this message or the help of the given subcommand(s)

Options:
      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin models --help
List the models available to your account

Usage: devin models [OPTIONS] [COMMAND]

Commands:
  list  List available models, organized by model family
  help  Print this message or the help of the given subcommand(s)

Options:
      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin doctor --help
Diagnose the local Devin configuration

Usage: devin doctor [OPTIONS]

Options:
      --json
          Emit machine-readable JSON instead of the text table

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin rules --help
Manage agent rules (always-on context blobs)

Usage: devin rules [OPTIONS] <COMMAND>

Commands:
  list   List all available rules
  show   Show details for a specific rule
  paths  Show rule directory locations
  help   Print this message or the help of the given subcommand(s)

Options:
      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin skills --help
Manage agent skills (slash commands and agent-triggered context blobs)

Usage: devin skills [OPTIONS] <COMMAND>

Commands:
  list   List all available skills
  show   Show details for a specific skill
  paths  Show skill directory locations
  help   Print this message or the help of the given subcommand(s)

Options:
      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin plugins --help
Manage plugins (install, list, info, update, remove)

Usage: devin plugins [OPTIONS] <COMMAND>

Commands:
  install  Install a plugin from a source (auto-installs its required plugins)
  list     List installed plugins with their version and blocked status
  info     Show a plugin's skills, hooks, rules, and its required/optional/forbidden lists
  update   Re-fetch and re-install a plugin (or all plugins) at the latest HEAD
  remove   Remove an installed plugin (and the auto-installed requireds nothing else needs)
  prune    Prune plugin requirements from repos that no longer exist on disk, then garbage-collect any plugin content no live scope still requires
  help     Print this message or the help of the given subcommand(s)

Options:
      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin cloud --help
Start a Devin Cloud session (same as --cloud) or manage cloud resources

With no subcommand, opens the REPL on Devin cloud sessions instead of the local agent. Subcommands manage Devin Cloud resources (environment setup, sandbox sessions, builds).

Usage: devin cloud [OPTIONS] [COMMAND]

Commands:
  drs   Manage Declarative Repo Setup (environment blueprints, sandbox sessions, and builds)
  help  Print this message or the help of the given subcommand(s)

Options:
      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin list --help
List sessions in the current directory

Usage: devin list [OPTIONS]

Options:
      --format <FORMAT>
          Output format

          Possible values:
          - interactive: Interactive session picker (default)
          - json:        JSON output
          - csv:         CSV output
          
          [default: interactive]

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin rm --help
Delete a session by id or name

Usage: devin rm [OPTIONS] <TARGET>

Arguments:
  <TARGET>
          Session id or name, or an unambiguous id prefix

Options:
      --force
          Skip the confirmation prompt

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin ssh --help
SSH into a cloud Devin session's box

Wraps `ssh` to the Devin SSH gateway and approves the connection with your logged-in credentials, so no browser round-trip is needed.

Usage: devin ssh [OPTIONS] [SESSION] [SSH_ARGS]...

Arguments:
  [SESSION]
          Session id or session URL (omit to pick from your cloud sessions)

  [SSH_ARGS]...
          Extra options passed through to ssh (e.g. `-L 8080:localhost:8080`)

Options:
      --gateway <GATEWAY>
          SSH gateway host (defaults to the ssh. host of your Devin API)

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin forward --help
Forward ports from a cloud Devin session's box to localhost

Opens a tunnel to the Devin SSH gateway and forwards each port so a dev server running on the box can be opened at http://localhost:<port>. Runs until Ctrl-C.

Usage: devin forward [OPTIONS] <SESSION> <PORTS>...

Arguments:
  <SESSION>
          Session id or session URL

  <PORTS>...
          Ports on the box to forward, as `<port>` or `<local>:<port>` (e.g. `3000` or `8080:3000`)

Options:
      --gateway <GATEWAY>
          SSH gateway host (defaults to the ssh. host of your Devin API)

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin update --help
Check for updates and optionally install them

Usage: devin update [OPTIONS]

Options:
      --force
          Force re-install even if already on the latest version

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin migrate --help
Migrate configuration from other tools

Usage: devin migrate [OPTIONS] <COMMAND>

Commands:
  hooks      Migrate Windsurf hooks (.windsurf/hooks.json) to Devin hooks (.devin/hooks.v1.json)
  workflows  Migrate workflow files into skills and remove the originals
  help       Print this message or the help of the given subcommand(s)

Options:
      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin sandbox --help
[Research Preview] Process sandboxing for the exec tool

Usage: devin sandbox [OPTIONS] <COMMAND>

Commands:
  setup  [Research Preview] Print the sandbox prerequisites for the current platform
  help   Print this message or the help of the given subcommand(s)

Options:
      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin setup --help
Interactive setup wizard

Guides you through authentication and MCP configuration.

Usage: devin setup [OPTIONS]

Options:
      --force-manual-token-flow
          Skip browser-based auth and paste a token manually
          
          Useful for remote or SSH sessions where the localhost redirect won't work.

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin acp --help
Run as an ACP (Agent Client Protocol) server over stdio

Usage: devin acp [OPTIONS]

Options:
      --agent-type <AGENT_TYPE>
          The type of agent to run. When omitted, runs the default agent

          Possible values:
          - summarizer: A summarizer agent with no tools. The model outputs the full summary as text and a `PostAgentIteration` cog persists it to `~/.local/share/devin/summaries/<session_id>.md` (i.e. `$XDG_DATA_HOME/devin/summaries/`)
          - review:     A code-review agent with read-only + shell tools. Reviews diffs and provides actionable feedback on correctness, style, security, performance, and completeness
          - codemap:    A code-map generation agent with read-only tools. Explores a codebase and emits the structured `<CODEMAP>` / trace output the Windsurf code map viewer parses; routes inference to the codemap model

      --model <MODEL>
          Default model for every new ACP session, overriding the enterprise-configured default. Accepts the same fuzzy names as `/model` (family slug, alias, or partial name), e.g. `--model opus`.
          
          Also read from `DEVIN_MODEL`, so the in-process ACP server the REPL spawns (a `devin acp` child that inherits the env) picks it up too.
          
          [env: DEVIN_MODEL=]

      --refusal-fallback <MODEL>
          Models to switch to, in order, when the provider refuses a request under its usage policy — Anthropic content policy, Anthropic cyber verification, or OpenAI cyber policy. Accepts the same fuzzy names as `--model`, and each candidate is tried at most once per session.
          
          Also read from `DEVIN_REFUSAL_FALLBACK` (comma-separated), so the `devin acp` child the REPL spawns picks it up too — that env var is the way to enable this for `devin` and `devin -p`, whose agent runs in that child.
          
          [env: DEVIN_REFUSAL_FALLBACK=]

      --cloud
          Relay to Devin cloud instead of running the local agent.
          
          Proxies the stdio ACP transport to the Devin cloud ACP WebSocket, so the connected host drives cloud Devin sessions. Requires a Devin account (sign in with `auth login`). `--agent-type` and `--model` only apply to the local agent and are ignored.

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin daemon --help
Manage the background agent daemon that `acp` clients share

Usage: devin daemon [OPTIONS] <COMMAND>

Commands:
  start   Start the daemon if it is not running
  stop    Stop the daemon; its loaded sessions are saved and unloaded. Busy sessions are stopped only with `--force` or after asking
  status  Show whether the daemon is running and how many sessions it holds
  help    Print this message or the help of the given subcommand(s)

Options:
      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin mcp add --help
Add a new MCP server.

Transport is inferred automatically when --transport is omitted: - If a URL is provided (positional or --url) → HTTP - If --command is provided or trailing args (after --) are present → stdio

For stdio servers, --command is optional when trailing args are provided; the first trailing arg becomes the command and the rest become its arguments.

Usage: devin mcp add --transport http <NAME> <URL>
       devin mcp add <NAME> -- <COMMAND> [ARGS...]
       devin mcp add <NAME> --url <URL>
       devin mcp add <NAME> --transport <stdio|sse|http> [OPTIONS]

Arguments:
  <NAME>
          Server name

  [POSITIONAL_URL]
          URL (positional, for HTTP/SSE transports)
          
          Can also be provided via --url. When both are given, --url takes precedence.

  [ARGS]...
          Arguments for stdio command
          
          Pass arguments after -- at the end of the command. When --command is omitted, the first argument is used as the command.

Options:
  -t, --transport <TRANSPORT>
          Transport type (inferred when omitted: URL → http, trailing args → stdio)

          Possible values:
          - stdio: Standard input/output (local process)
          - sse:   Server-Sent Events (HTTP streaming)
          - http:  HTTP request/response

  -s, --scope <SCOPE>
          Configuration scope

          Possible values:
          - local:   Local project config, not committed (.devin/mcp_config.local.json)
          - project: Project-level config (.devin/mcp_config.json)
          - user:    User-level config (~/.config/devin/mcp_config.json)
          
          [default: local]

      --url <URL>
          URL for HTTP/SSE transports (alternative to positional URL)

      --command <COMMAND>
          Command for stdio transport (optional when trailing args are provided)

  -e, --env <KEY=VALUE>
          Environment variables
          
          Format: KEY=value. Can be specified multiple times.

  -H, --header <HEADER: VALUE>
          HTTP headers
          
          Format: "Header: value". Can be specified multiple times.

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

      --scopes <SCOPE,SCOPE>
          OAuth scopes to request (comma-separated)
          
          Only used for HTTP/SSE servers that support OAuth.

      --oauth-client-id <ID>
          Pre-registered OAuth client ID for servers that don't support dynamic client registration (e.g. GitHub)

      --oauth-client-secret <SECRET>
          OAuth client secret (for confidential clients)

      --oauth-resource <RESOURCE>
          Override the RFC 8707 `resource` parameter sent in OAuth requests (default: the MCP server URL). Pass an empty string to omit the parameter for providers that reject it (e.g. Microsoft Entra)

  -h, --help
          Print help (see a summary with '-h')
=================== devin cloud drs --help
Manage Declarative Repo Setup (environment blueprints, sandbox sessions, and builds)

Usage: devin cloud drs [OPTIONS] <COMMAND>

Commands:
  whoami            Print the current DRS configuration (org, API endpoint, auth status)
  sandbox-create    Create a sandbox Devin session for testing repo setup
  run               Run a shell command inside a sandbox session
  blueprint-list    List all environment blueprints for the organization
  blueprint-create  Create a new environment blueprint
  blueprint-write   Update an existing environment blueprint
  build             Trigger an environment build and wait for it to finish
  build-start       Trigger an environment build without waiting
  build-wait        Wait for a previously started build to finish
  build-logs        Fetch the log stream for a build job
  secret-create     Create organization-level secrets
  help              Print this message or the help of the given subcommand(s)

Options:
      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin plugins install --help
Install a plugin from a source (auto-installs its required plugins).

A source is a GitHub `owner/repo`, a git URL, or a local path. Append `#path/to/plugin` when the plugin is below a git repository's root. A repository with no plugin manifest but skills under `skills/<name>/SKILL.md` installs as a skills-only plugin named after the repository.

Usage: devin plugins install [OPTIONS] <SOURCE>

Arguments:
  <SOURCE>
          The plugin source (e.g. `acme/review-tools`, `acme/plugins#plugins/review`, a git URL, or `./path`)

Options:
  -y, --yes
          Skip the interactive trust prompt

      --local
          Install on this machine only, without adding the plugin to your personal plugins

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin skills list --help
List all available skills

Usage: devin skills list [OPTIONS]

Options:
      --trigger <TRIGGER>
          Filter by trigger type

          Possible values:
          - user:  User-invocable skills (slash commands)
          - model: Model-invocable skills

      --json
          Output as JSON

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin auth login --help
Log in to Devin

Usage: devin auth login [OPTIONS]

Options:
      --force-manual-token-flow
          Skip browser-based auth and paste a token manually
          
          Useful for remote or SSH sessions where the localhost redirect won't work.

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
=================== devin daemon stop --help
Stop the daemon; its loaded sessions are saved and unloaded. Busy sessions are stopped only with `--force` or after asking

Usage: devin daemon stop [OPTIONS]

Options:
      --force
          

      --process-model <PROCESS_MODEL>
          Where local agents run
          
          Overrides the `process_model` setting, or the default, for this run.

          Possible values:
          - daemon:     In the background daemon, shared by `devin` commands and started if needed
          - in-process: Inside this command, with no background daemon

  -h, --help
          Print help (see a summary with '-h')
```
