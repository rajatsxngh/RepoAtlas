const path = require("path");
const { format } = require("util");

exports.join = function join(...parts) {
  return path.join(...parts);
};

function describePath(p) {
  return format("%s (%s)", p, path.extname(p));
}

module.exports.describePath = describePath;
