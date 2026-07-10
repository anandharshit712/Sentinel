"use strict";

const express = require("express");
const { createConnection, initSchema } = require("./db");
const { AuthError, authenticate, hashPassword } = require("./auth/login");
const { CatalogError, addItem } = require("./catalog/processor");

function createApp(conn = createConnection()) {
  const app = express();
  app.use(express.json());

  app.get("/health", (req, res) => res.json({ status: "ok" }));

  app.post("/login", (req, res) => {
    try {
      const userId = authenticate(req.body.username, req.body.password, conn);
      res.json({ user_id: userId });
    } catch (err) {
      if (err instanceof AuthError) return res.status(401).json({ error: "unauthorized" });
      throw err;
    }
  });

  app.post("/items", (req, res) => {
    try {
      const record = addItem(req.body.username, req.body.password, req.body.item, conn);
      res.json({ item: record });
    } catch (err) {
      if (err instanceof AuthError || err instanceof CatalogError) {
        return res.status(400).json({ error: err.message });
      }
      throw err;
    }
  });

  return app;
}

if (require.main === module) {
  const conn = createConnection();
  initSchema(conn, [{ username: "bob", passwordHash: hashPassword("pw") }]);
  createApp(conn).listen(3000, () => console.log("listening on :3000"));
}

module.exports = { createApp };
