"use strict";

// Catalog processing — authenticates then records a catalog item.
const { authenticate } = require("../auth/login");

class CatalogError extends Error {}

function addItem(username, password, item, conn) {
  authenticate(username, password, conn);
  if (!item || !item.name) {
    throw new CatalogError("item name is required");
  }
  const record = { id: conn.catalog.length + 1, name: item.name, price: item.price || 0 };
  conn.catalog.push(record);
  return record;
}

module.exports = { CatalogError, addItem };
