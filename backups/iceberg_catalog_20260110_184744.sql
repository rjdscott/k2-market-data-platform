--
-- PostgreSQL database dump
--

\restrict HnmT28CXPEeR6NS3cDlKXx1ZlN2Ng4lrmHvWbO0Gvdh1dF4px3Tpi7cDOlzeeNf

-- Dumped from database version 16.11
-- Dumped by pg_dump version 16.11

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: iceberg
--

CREATE TABLE public.audit_log (
    id integer NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now(),
    user_id character varying(255),
    action character varying(50),
    resource_type character varying(50),
    resource_name character varying(255),
    metadata jsonb,
    ip_address inet,
    success boolean DEFAULT true,
    error_message text
);


ALTER TABLE public.audit_log OWNER TO iceberg;

--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: iceberg
--

CREATE SEQUENCE public.audit_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.audit_log_id_seq OWNER TO iceberg;

--
-- Name: audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: iceberg
--

ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id;


--
-- Name: data_lineage; Type: TABLE; Schema: public; Owner: iceberg
--

CREATE TABLE public.data_lineage (
    id integer NOT NULL,
    source_table character varying(255),
    target_table character varying(255),
    transformation_type character varying(50),
    created_at timestamp with time zone DEFAULT now(),
    metadata jsonb
);


ALTER TABLE public.data_lineage OWNER TO iceberg;

--
-- Name: data_lineage_id_seq; Type: SEQUENCE; Schema: public; Owner: iceberg
--

CREATE SEQUENCE public.data_lineage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.data_lineage_id_seq OWNER TO iceberg;

--
-- Name: data_lineage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: iceberg
--

ALTER SEQUENCE public.data_lineage_id_seq OWNED BY public.data_lineage.id;


--
-- Name: iceberg_namespace_properties; Type: TABLE; Schema: public; Owner: iceberg
--

CREATE TABLE public.iceberg_namespace_properties (
    catalog_name character varying(255) NOT NULL,
    namespace character varying(255) NOT NULL,
    property_key character varying(255) NOT NULL,
    property_value character varying(1000)
);


ALTER TABLE public.iceberg_namespace_properties OWNER TO iceberg;

--
-- Name: iceberg_tables; Type: TABLE; Schema: public; Owner: iceberg
--

CREATE TABLE public.iceberg_tables (
    catalog_name character varying(255) NOT NULL,
    table_namespace character varying(255) NOT NULL,
    table_name character varying(255) NOT NULL,
    metadata_location character varying(1000),
    previous_metadata_location character varying(1000)
);


ALTER TABLE public.iceberg_tables OWNER TO iceberg;

--
-- Name: user_roles; Type: TABLE; Schema: public; Owner: iceberg
--

CREATE TABLE public.user_roles (
    id integer NOT NULL,
    user_id character varying(255) NOT NULL,
    role character varying(50) NOT NULL,
    namespace character varying(255),
    created_at timestamp with time zone DEFAULT now(),
    created_by character varying(255)
);


ALTER TABLE public.user_roles OWNER TO iceberg;

--
-- Name: user_roles_id_seq; Type: SEQUENCE; Schema: public; Owner: iceberg
--

CREATE SEQUENCE public.user_roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.user_roles_id_seq OWNER TO iceberg;

--
-- Name: user_roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: iceberg
--

ALTER SEQUENCE public.user_roles_id_seq OWNED BY public.user_roles.id;


--
-- Name: audit_log id; Type: DEFAULT; Schema: public; Owner: iceberg
--

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);


--
-- Name: data_lineage id; Type: DEFAULT; Schema: public; Owner: iceberg
--

ALTER TABLE ONLY public.data_lineage ALTER COLUMN id SET DEFAULT nextval('public.data_lineage_id_seq'::regclass);


--
-- Name: user_roles id; Type: DEFAULT; Schema: public; Owner: iceberg
--

ALTER TABLE ONLY public.user_roles ALTER COLUMN id SET DEFAULT nextval('public.user_roles_id_seq'::regclass);


--
-- Data for Name: audit_log; Type: TABLE DATA; Schema: public; Owner: iceberg
--

COPY public.audit_log (id, "timestamp", user_id, action, resource_type, resource_name, metadata, ip_address, success, error_message) FROM stdin;
1	2026-01-09 22:30:50.178364+00	system	CREATE	DATABASE	iceberg_catalog	{"event": "database_initialized"}	\N	t	\N
\.


--
-- Data for Name: data_lineage; Type: TABLE DATA; Schema: public; Owner: iceberg
--

COPY public.data_lineage (id, source_table, target_table, transformation_type, created_at, metadata) FROM stdin;
\.


--
-- Data for Name: iceberg_namespace_properties; Type: TABLE DATA; Schema: public; Owner: iceberg
--

COPY public.iceberg_namespace_properties (catalog_name, namespace, property_key, property_value) FROM stdin;
\.


--
-- Data for Name: iceberg_tables; Type: TABLE DATA; Schema: public; Owner: iceberg
--

COPY public.iceberg_tables (catalog_name, table_namespace, table_name, metadata_location, previous_metadata_location) FROM stdin;
rest_backend	market_data	quotes	s3://warehouse/market_data/quotes/metadata/00003-a1cc89e8-f121-4026-9fb1-2cd1bb7d0cda.gz.metadata.json	s3://warehouse/market_data/quotes/metadata/00002-af3991ab-1713-4541-b155-d0b437ddb707.gz.metadata.json
rest_backend	market_data	trades	s3://warehouse/market_data/trades/metadata/00007-eebd7f64-2c09-497c-b3e4-89a47e4a9080.gz.metadata.json	s3://warehouse/market_data/trades/metadata/00006-c6907d0b-b844-45d0-88d2-075f602a4bd1.gz.metadata.json
\.


--
-- Data for Name: user_roles; Type: TABLE DATA; Schema: public; Owner: iceberg
--

COPY public.user_roles (id, user_id, role, namespace, created_at, created_by) FROM stdin;
1	admin	admin	\N	2026-01-09 22:30:50.174871+00	system
\.


--
-- Name: audit_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: iceberg
--

SELECT pg_catalog.setval('public.audit_log_id_seq', 1, true);


--
-- Name: data_lineage_id_seq; Type: SEQUENCE SET; Schema: public; Owner: iceberg
--

SELECT pg_catalog.setval('public.data_lineage_id_seq', 1, false);


--
-- Name: user_roles_id_seq; Type: SEQUENCE SET; Schema: public; Owner: iceberg
--

SELECT pg_catalog.setval('public.user_roles_id_seq', 1, true);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: iceberg
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: data_lineage data_lineage_pkey; Type: CONSTRAINT; Schema: public; Owner: iceberg
--

ALTER TABLE ONLY public.data_lineage
    ADD CONSTRAINT data_lineage_pkey PRIMARY KEY (id);


--
-- Name: iceberg_namespace_properties iceberg_namespace_properties_pkey; Type: CONSTRAINT; Schema: public; Owner: iceberg
--

ALTER TABLE ONLY public.iceberg_namespace_properties
    ADD CONSTRAINT iceberg_namespace_properties_pkey PRIMARY KEY (catalog_name, namespace, property_key);


--
-- Name: iceberg_tables iceberg_tables_pkey; Type: CONSTRAINT; Schema: public; Owner: iceberg
--

ALTER TABLE ONLY public.iceberg_tables
    ADD CONSTRAINT iceberg_tables_pkey PRIMARY KEY (catalog_name, table_namespace, table_name);


--
-- Name: user_roles user_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: iceberg
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_pkey PRIMARY KEY (id);


--
-- Name: user_roles user_roles_user_id_namespace_key; Type: CONSTRAINT; Schema: public; Owner: iceberg
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_user_id_namespace_key UNIQUE (user_id, namespace);


--
-- Name: idx_audit_log_resource; Type: INDEX; Schema: public; Owner: iceberg
--

CREATE INDEX idx_audit_log_resource ON public.audit_log USING btree (resource_type, resource_name);


--
-- Name: idx_audit_log_timestamp; Type: INDEX; Schema: public; Owner: iceberg
--

CREATE INDEX idx_audit_log_timestamp ON public.audit_log USING btree ("timestamp");


--
-- Name: idx_audit_log_user; Type: INDEX; Schema: public; Owner: iceberg
--

CREATE INDEX idx_audit_log_user ON public.audit_log USING btree (user_id);


--
-- Name: idx_lineage_source; Type: INDEX; Schema: public; Owner: iceberg
--

CREATE INDEX idx_lineage_source ON public.data_lineage USING btree (source_table);


--
-- Name: idx_lineage_target; Type: INDEX; Schema: public; Owner: iceberg
--

CREATE INDEX idx_lineage_target ON public.data_lineage USING btree (target_table);


--
-- Name: idx_user_roles_user; Type: INDEX; Schema: public; Owner: iceberg
--

CREATE INDEX idx_user_roles_user ON public.user_roles USING btree (user_id);


--
-- PostgreSQL database dump complete
--

\unrestrict HnmT28CXPEeR6NS3cDlKXx1ZlN2Ng4lrmHvWbO0Gvdh1dF4px3Tpi7cDOlzeeNf

