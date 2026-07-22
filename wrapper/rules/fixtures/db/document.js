const nativeItems = db.collection("inventory_items");
const Item = mongoose.model("InventoryItem", schema, "inventory_items");
const Unresolved = mongoose.model("UnresolvedItem", schema);
const settingsSchema = new mongoose.Schema({}, { collection: "runtime_settings" });
const dynamicItems = db.collection(collectionName);
const DynamicModel = mongoose.model("DynamicItem", schema, collectionName);
