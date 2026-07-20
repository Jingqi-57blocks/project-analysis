module.exports = {
  up: (q) => q.createTable('widgets', { id: {} }),  // declaration + schema write
  down: (q) => q.dropTable('widgets'),              // schema write
};
const model = { modelName: 'Gadget', tableName: 'gadgets' };  // declaration
