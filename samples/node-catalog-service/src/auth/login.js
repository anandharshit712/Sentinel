"use strict";

// Authentication — the sensitive module. Baseline is safe.
const crypto = require("crypto");

class AuthError extends Error {}

function hashPassword(password) {
  return crypto.createHash("sha256").update(password).digest("hex");
}

function authenticate(username, password, conn) {
  const user = conn.users.get(username);
  if (!user || user.passwordHash !== hashPassword(password)) {
    throw new AuthError("invalid credentials");
  }
  return username;
}

module.exports = { AuthError, hashPassword, authenticate };
