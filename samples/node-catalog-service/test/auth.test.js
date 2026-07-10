"use strict";

const { createConnection, initSchema } = require("../src/db");
const { AuthError, authenticate, hashPassword } = require("../src/auth/login");

function seededConn() {
  const conn = createConnection();
  initSchema(conn, [{ username: "bob", passwordHash: hashPassword("pw") }]);
  return conn;
}

test("authenticate succeeds with correct password", () => {
  expect(authenticate("bob", "pw", seededConn())).toBe("bob");
});

test("authenticate rejects wrong password", () => {
  expect(() => authenticate("bob", "wrong", seededConn())).toThrow(AuthError);
});

test("authenticate rejects unknown user", () => {
  expect(() => authenticate("nobody", "pw", seededConn())).toThrow(AuthError);
});
