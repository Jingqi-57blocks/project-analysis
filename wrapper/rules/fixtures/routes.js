const router = require('express').Router();
router.get('/widgets', listWidgets);         // POSITIVE
app.post('/gadgets/:id', createGadget);       // POSITIVE
app.use('/mounted', subRouter);               // NEGATIVE (mount)
[1, 2].map((x) => x + 1);                      // NEGATIVE (not a route)
