"use strict";

// Tiny in-memory store — mirrors the Python sample's sqlite-in-memory use, no real DB needed.
function createConnection() {
  return { users: new Map(), catalog: [] };
}

function initSchema(conn, seedUsers = []) {
  for (const { username, passwordHash } of seedUsers) {
    conn.users.set(username, { passwordHash });
  }
}

module.exports = { createConnection, initSchema };
