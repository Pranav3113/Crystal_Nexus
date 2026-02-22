-- MySQL dump 10.13  Distrib 9.5.0, for macos15.4 (arm64)
--
-- Host: localhost    Database: crystal_nexus
-- ------------------------------------------------------
-- Server version	9.5.0

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `menus`
--

DROP TABLE IF EXISTS `menus`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `menus` (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(120) NOT NULL,
  `icon` varchar(64) DEFAULT NULL,
  `sort_order` int DEFAULT NULL,
  `is_active` tinyint(1) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=17 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `menus`
--

LOCK TABLES `menus` WRITE;
/*!40000 ALTER TABLE `menus` DISABLE KEYS */;
INSERT INTO `menus` VALUES (10,'Dashboard','speedometer2',5,1,'2026-02-16 04:22:59'),(11,'Sales','bar-chart',10,1,'2026-02-16 04:22:59'),(12,'Quotes','receipt',20,1,'2026-02-16 04:22:59'),(13,'Finance','cash-stack',30,1,'2026-02-16 04:22:59'),(14,'Masters','sliders',40,1,'2026-02-16 04:22:59'),(15,'Admin','gear',90,1,'2026-02-16 04:22:59'),(16,'System','shield-check',100,1,'2026-02-16 04:22:59');
/*!40000 ALTER TABLE `menus` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `submenus`
--

DROP TABLE IF EXISTS `submenus`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `submenus` (
  `id` int NOT NULL AUTO_INCREMENT,
  `menu_id` int NOT NULL,
  `title` varchar(120) NOT NULL,
  `endpoint` varchar(160) DEFAULT NULL,
  `url` varchar(255) DEFAULT NULL,
  `icon` varchar(64) DEFAULT NULL,
  `sort_order` int DEFAULT NULL,
  `is_active` tinyint(1) DEFAULT NULL,
  `permission_code` varchar(120) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `menu_id` (`menu_id`),
  CONSTRAINT `submenus_ibfk_1` FOREIGN KEY (`menu_id`) REFERENCES `menus` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=96 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `submenus`
--

LOCK TABLES `submenus` WRITE;
/*!40000 ALTER TABLE `submenus` DISABLE KEYS */;
INSERT INTO `submenus` VALUES (60,10,'Dashboard','admin.dashboard',NULL,NULL,1,1,'admin.dashboard.view','2026-02-16 04:22:59'),(61,11,'Leads','leads.list_leads',NULL,NULL,1,1,'leads.view','2026-02-16 04:22:59'),(62,11,'Pipeline','pipeline.board',NULL,NULL,2,1,'pipeline.view','2026-02-16 04:22:59'),(63,11,'Clients','clients.list_clients',NULL,NULL,3,1,'clients.manage','2026-02-16 04:22:59'),(64,12,'Quotes','quotes.list_quotes',NULL,NULL,1,1,'quotes.view','2026-02-16 04:22:59'),(65,12,'Proposals Sent','quotes.sent_proposals',NULL,NULL,2,1,'quotes.proposals_sent.view','2026-02-16 04:22:59'),(66,12,'Approvals Inbox','quotes.approvals_inbox',NULL,NULL,3,1,'quotes.approve','2026-02-16 04:22:59'),(67,12,'Approval Rules','quotes.approval_rules_master',NULL,NULL,4,1,'approval_rules.manage','2026-02-16 04:22:59'),(68,13,'PI Requests','proforma.pi_requests',NULL,NULL,1,1,'proforma.requests.view','2026-02-16 04:22:59'),(69,13,'Proforma Invoices','proforma.list_pi',NULL,NULL,2,1,'proforma.view','2026-02-16 04:22:59'),(70,13,'Invoice Requests','invoices.invoice_requests',NULL,NULL,3,1,'invoices.requests.view','2026-02-16 04:22:59'),(71,13,'Invoices','invoices.list_invoices',NULL,NULL,4,1,'invoices.view','2026-02-16 04:22:59'),(72,13,'Payments Queue','payments.finance_payment_queue',NULL,NULL,5,1,'payments.verify','2026-02-16 04:22:59'),(73,14,'Lead Status','admin.lead_status_master',NULL,NULL,1,1,'masters.manage','2026-02-16 04:22:59'),(74,14,'Lead Source','admin.lead_source_master',NULL,NULL,2,1,'masters.manage','2026-02-16 04:22:59'),(75,14,'Activity Types','admin.activity_type_master',NULL,NULL,3,1,'masters.manage','2026-02-16 04:22:59'),(76,14,'Industries','industries.industries_master',NULL,NULL,4,1,'industries.manage','2026-02-16 04:22:59'),(77,14,'Company','company_master.company_master',NULL,NULL,5,1,'company.view','2026-02-16 04:22:59'),(78,15,'User Master','user_master.users_master',NULL,NULL,1,1,'users.manage','2026-02-16 04:22:59'),(79,15,'Designations','designations.designation_master',NULL,NULL,2,1,'designations.manage','2026-02-16 04:22:59'),(80,15,'Roles','rbac.roles_master',NULL,NULL,3,1,'roles.manage','2026-02-16 04:22:59'),(81,15,'Permissions','rbac.permissions_master',NULL,NULL,4,1,'permissions.manage','2026-02-16 04:22:59'),(82,15,'Menu Management','menu_master.menu_management',NULL,NULL,5,1,'menus.manage','2026-02-16 04:22:59'),(83,16,'Audit Logs','admin.audit_logs',NULL,NULL,1,1,'admin.audit.view','2026-02-16 04:22:59'),(84,16,'Logout','auth.logout',NULL,NULL,999,1,NULL,'2026-02-16 04:22:59'),(86,14,'Currency Master','currencies.currencies_master',NULL,NULL,1,1,'currencies.manage','2026-02-16 09:44:18'),(87,10,'Cluster Productivity',NULL,'/reports/cluster/productivity',NULL,1,1,'admin.dashboard.view','2026-02-16 11:11:18'),(89,10,'Collections & Aging',NULL,'/reports/cluster/collections-aging',NULL,1,1,NULL,'2026-02-16 11:46:34'),(90,10,'Cluster Margin Quality',NULL,'/reports/cluster/margin-quality',NULL,1,1,'admin.dashboard.view','2026-02-16 11:57:59'),(91,14,'Margin Settings',NULL,'/admin/margin-settings',NULL,1,1,'masters.manage','2026-02-16 12:04:34'),(92,10,'Pipeline vs Conversion',NULL,'/reports/cluster/pipeline-conversion',NULL,1,1,'admin.dashboard.view','2026-02-18 04:46:38'),(93,10,'Account Health',NULL,'/reports/cluster/account-health',NULL,1,1,'admin.dashboard.view','2026-02-18 04:47:02'),(94,14,'Cluster Master','admin.cluster_master',NULL,NULL,1,1,'clusters.manage','2026-02-18 14:05:53'),(95,10,'My Dashboard',NULL,'/reports/my-dashboard',NULL,1,1,NULL,'2026-02-19 04:36:11');
/*!40000 ALTER TABLE `submenus` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-02-22 12:10:12
