#!/usr/bin/env node
import { runCli } from './cli/commands.mjs';

process.exitCode = await runCli(process.argv.slice(2));
