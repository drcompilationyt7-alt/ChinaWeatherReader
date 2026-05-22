/**
 * Mr. WorldWideWebster - Logger
 * Simple colored logging utility
 */
const chalk = require('chalk');

class Logger {
  constructor(name) {
    this.name = name;
  }

  info(msg, data = null) {
    console.log(chalk.blue(`[${this.name}] ℹ️  ${msg}`), data ? '\n' + JSON.stringify(data, null, 2) : '');
  }

  success(msg, data = null) {
    console.log(chalk.green(`[${this.name}] ✅ ${msg}`), data ? '\n' + JSON.stringify(data, null, 2) : '');
  }

  warn(msg, data = null) {
    console.log(chalk.yellow(`[${this.name}] ⚠️  ${msg}`), data ? '\n' + JSON.stringify(data, null, 2) : '');
  }

  error(msg, data = null) {
    console.log(chalk.red(`[${this.name}] ❌ ${msg}`), data ? '\n' + JSON.stringify(data, null, 2) : '');
  }

  header(msg) {
    console.log(chalk.cyan.bold(`\n═══════════════════════════════════════════`));
    console.log(chalk.cyan.bold(`  ${msg}`));
    console.log(chalk.cyan.bold(`═══════════════════════════════════════════\n`));
  }
}

module.exports = { Logger };