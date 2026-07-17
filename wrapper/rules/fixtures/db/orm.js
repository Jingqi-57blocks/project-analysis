module.exports = {
  up: (q) => q.createTable('widgets', { id: {} }),  // declaration + write
  down: (q) => q.dropTable('widgets'),              // write
};
const model = { modelName: 'Gadget', tableName: 'gadgets' };  // declaration
