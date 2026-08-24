package com.example.model;

import org.springframework.data.mongodb.core.index.Indexed;
import org.springframework.data.mongodb.core.index.CompoundIndex;
import org.springframework.data.mongodb.core.mapping.Document;

@Document(collection = "users")
@CompoundIndex(name = "email_tenant_idx", def = "{'email': 1, 'tenantId': 1}")
@CompoundIndex(name = "status_created_idx", def = "{'status': 1, 'createdAt': -1}")
public class SampleEntity {

    @Indexed(unique = true)
    private String email;

    @Indexed
    private String tenantId;

    @TextIndexed
    private String description;

    private String name;
}
