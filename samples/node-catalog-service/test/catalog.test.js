"use strict";

const { createConnection, initSchema } = require("../src/db");
const { hashPassword } = require("../src/auth/login");
const { CatalogError, addItem } = require("../src/catalog/processor");
const { AuthError } = require("../src/auth/login");

function seededConn() {
  const conn = createConnection();
  initSchema(conn, [{ username: "bob", passwordHash: hashPassword("pw") }]);
  return conn;
}

test("addItem records an item after authenticating", () => {
  const conn = seededConn();
  const record = addItem("bob", "pw", { name: "widget", price: 5 }, conn);
  expect(record).toEqual({ id: 1, name: "widget", price: 5 });
  expect(conn.catalog).toHaveLength(1);
});

test("addItem rejects an item with no name", () => {
  const conn = seededConn();
  expect(() => addItem("bob", "pw", {}, conn)).toThrow(CatalogError);
});

test("addItem rejects bad credentials", () => {
  const conn = seededConn();
  expect(() => addItem("bob", "wrong", { name: "widget" }, conn)).toThrow(AuthError);
});
