import express from "express";
import { Controller, Get, Post } from "@nestjs/common";
const { authenticate } = require("./auth");

const app = express();
const router = express.Router();

export const listOrders = async (req, res) => {
  res.json(await db.orders.findAll());
};

router.get("/orders", authenticate, listOrders);
router.post("/orders", (req, res) => res.status(201).end());
router
  .route("/orders/:id")
  .get(getOrder)
  .delete(removeOrder);

app.use("/api", router);

// Not a route: a Map lookup.
const cache = new Map();
cache.get("/orders");

@Controller("users")
export class UsersController {
  @Get(":id")
  findOne(id: string) {
    return id;
  }

  @Post()
  async create(body: unknown) {
    return body;
  }
}
