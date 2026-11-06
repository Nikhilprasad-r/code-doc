package main

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

type Server struct {
	router *gin.Engine
}

func (s *Server) Routes() {
	s.router.GET("/health", s.health)
	s.router.POST("/jobs", s.createJob)
}

func main() {
	http.HandleFunc("GET /metrics", metricsHandler)
}
